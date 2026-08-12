"""
xi dll ffximain crashdump
=====================
Parse a Windows minidump (.dmp) from Local\\CrashDumps (or any path) and write
a human overview + JSON next to the dump.

Focuses on FFXI / pol.exe crashes: exception, threads, modules, and optional
disassembly around the fault if FFXiMain is mapped and a local DLL is available.
"""
from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from xi.xi_config import XI_TOOLS_DIR

# ── Minidump stream types ────────────────────────────────────────────────────

STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6
STREAM_SYSTEM_INFO = 7
STREAM_MEMORY64_LIST = 9
STREAM_UNLOADED_MODULE_LIST = 14
STREAM_MISC_INFO = 15
STREAM_MEMORY_INFO_LIST = 16
STREAM_THREAD_INFO_LIST = 17

STREAM_NAMES = {
    0: "Unused",
    3: "ThreadList",
    4: "ModuleList",
    5: "MemoryList",
    6: "Exception",
    7: "SystemInfo",
    8: "ThreadExList",
    9: "Memory64List",
    10: "CommentA",
    11: "CommentW",
    12: "HandleData",
    13: "FunctionTable",
    14: "UnloadedModuleList",
    15: "MiscInfo",
    16: "MemoryInfoList",
    17: "ThreadInfoList",
    21: "SystemMemoryInfo",
    22: "ProcessVmCounters",
}

# Common NTSTATUS / exception codes
EXCEPTION_NAMES = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
    0xC0000096: "PRIVILEGED_INSTRUCTION",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000409: "STACK_BUFFER_OVERRUN",
    0x80000003: "BREAKPOINT",
    0x80000004: "SINGLE_STEP",
    0xC0000374: "HEAP_CORRUPTION",
    0xC0000135: "DLL_NOT_FOUND",
    0xC0000139: "ENTRYPOINT_NOT_FOUND",
    0xC0000142: "DLL_INIT_FAILED",
    0xE06D7363: "CPP_EH_EXCEPTION",  # "msc"
}

DEFAULT_DUMP_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "CrashDumps"
FFXIMAIN_PREFERRED_BASE = 0x10000000


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ModuleInfo:
    name: str
    base: int
    size: int
    end: int
    timestamp: int | None = None
    checksum: int | None = None
    version: str | None = None
    path: str | None = None


@dataclass
class ThreadInfo:
    thread_id: int
    suspend_count: int
    priority_class: int
    priority: int
    teb: int
    eip: int | None = None
    esp: int | None = None
    ebp: int | None = None
    eax: int | None = None
    ebx: int | None = None
    ecx: int | None = None
    edx: int | None = None
    esi: int | None = None
    edi: int | None = None
    eflags: int | None = None
    is_exception_thread: bool = False
    stack_words: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ExceptionInfo:
    code: int
    code_name: str
    flags: int
    record: int
    address: int
    parameters: list[int]
    access_type: str | None = None  # read/write/dep for AV
    access_address: int | None = None
    thread_id: int | None = None


@dataclass
class CrashReport:
    dump_path: str
    dump_size: int
    dump_mtime: str
    signature: str
    version: int
    flags: int
    timestamp: int | None
    timestamp_iso: str | None
    streams: list[dict[str, Any]]
    system: dict[str, Any]
    exception: dict[str, Any] | None
    modules: list[dict[str, Any]]
    unloaded_modules: list[dict[str, Any]]
    threads: list[dict[str, Any]]
    fault_module: dict[str, Any] | None
    ffximain: dict[str, Any] | None
    disasm: list[dict[str, Any]]
    notes: list[str]


# ── Low-level helpers ────────────────────────────────────────────────────────

def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def _rva_slice(data: bytes, rva: int, size: int) -> bytes:
    if rva < 0 or size < 0 or rva + size > len(data):
        raise ValueError(f"RVA out of range: rva=0x{rva:x} size={size} file={len(data)}")
    return data[rva : rva + size]


def _read_utf16z(data: bytes, rva: int, max_chars: int = 520) -> str:
    if rva <= 0 or rva >= len(data):
        return ""
    end = min(len(data), rva + max_chars * 2)
    chunk = data[rva:end]
    # find double-NUL
    out = bytearray()
    for i in range(0, len(chunk) - 1, 2):
        if chunk[i] == 0 and chunk[i + 1] == 0:
            break
        out += chunk[i : i + 2]
    try:
        return out.decode("utf-16-le", errors="replace")
    except Exception:
        return ""


def _filetime_to_iso(ft: int) -> str | None:
    # Windows FILETIME: 100ns since 1601-01-01
    if ft <= 0:
        return None
    try:
        # seconds between 1601 and 1970
        unix = ft / 10_000_000 - 11_644_473_600
        return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _ts_to_iso(ts: int) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _hex(v: int | None, width: int = 8) -> str:
    if v is None:
        return "n/a"
    return f"0x{v:0{width}X}"


def _exception_name(code: int) -> str:
    return EXCEPTION_NAMES.get(code & 0xFFFFFFFF, f"UNKNOWN_{code & 0xFFFFFFFF:08X}")


# ── Stream parsers ───────────────────────────────────────────────────────────

def parse_header(data: bytes) -> dict[str, Any]:
    if len(data) < 32 or data[:4] != b"MDMP":
        raise ValueError("Not a Windows minidump (missing MDMP signature)")
    sig, ver, nstreams, dir_rva, checksum, timestamp = struct.unpack_from("<4sIIIII", data, 0)
    flags = _u64(data, 24)
    return {
        "signature": sig.decode("ascii", errors="replace"),
        "version": ver,
        "number_of_streams": nstreams,
        "stream_directory_rva": dir_rva,
        "checksum": checksum,
        "timestamp": timestamp,
        "flags": flags,
    }


def parse_directory(data: bytes, dir_rva: int, nstreams: int) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    off = dir_rva
    for _ in range(nstreams):
        st, size, rva = struct.unpack_from("<III", data, off)
        off += 12
        out.append((st, size, rva))
    return out


def parse_system_info(blob: bytes) -> dict[str, Any]:
    # MINIDUMP_SYSTEM_INFO
    # USHORT ProcessorArchitecture
    # USHORT ProcessorLevel
    # USHORT ProcessorRevision
    # UCHAR  NumberOfProcessors
    # UCHAR  ProductType
    # ULONG32 MajorVersion / MinorVersion / BuildNumber / PlatformId
    # RVA CSDVersionRva
    if len(blob) < 32:
        return {"raw_size": len(blob)}
    processor_arch, processor_level, processor_revision = struct.unpack_from("<HHH", blob, 0)
    number_of_processors, product_type = struct.unpack_from("<BB", blob, 6)
    major, minor, build, platform_id, csd_rva = struct.unpack_from("<IIIII", blob, 8)
    arch = {0: "x86", 5: "ARM", 9: "x64", 12: "ARM64"}.get(processor_arch, str(processor_arch))
    return {
        "processor_architecture": arch,
        "processor_architecture_id": processor_arch,
        "processor_level": processor_level,
        "processor_revision": processor_revision,
        "number_of_processors": number_of_processors,
        "product_type": product_type,
        "os_major": major,
        "os_minor": minor,
        "os_build": build,
        "platform_id": platform_id,
        "csd_version_rva": csd_rva,
        "os_version": f"{major}.{minor}.{build}",
    }


def parse_misc_info(blob: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {"size_of_info": _u32(blob, 0) if len(blob) >= 4 else 0}
    if len(blob) < 24:
        return info
    flags = _u32(blob, 4)
    info["flags"] = flags
    info["process_id"] = _u32(blob, 8)
    info["process_create_time"] = _u32(blob, 12)
    info["process_create_time_iso"] = _ts_to_iso(_u32(blob, 12))
    info["process_user_time"] = _u32(blob, 16)
    info["process_kernel_time"] = _u32(blob, 20)
    if len(blob) >= 44 and (flags & 0x00000004):  # MINIDUMP_MISC1_PROCESSOR_POWER_INFO etc.
        pass
    return info


def parse_module_list(data: bytes, blob: bytes, base_rva: int) -> list[ModuleInfo]:
    if len(blob) < 4:
        return []
    count = _u32(blob, 0)
    modules: list[ModuleInfo] = []
    # Each MINIDUMP_MODULE is 108 bytes on x86 struct packing
    # ULONG64 BaseOfImage (8)
    # ULONG32 SizeOfImage (4)
    # ULONG32 CheckSum (4)
    # ULONG32 TimeDateStamp (4)
    # RVA ModuleNameRva (4)
    # VS_FIXEDFILEINFO VersionInfo (52)
    # MINIDUMP_LOCATION_DESCRIPTOR CvRecord (8)
    # MINIDUMP_LOCATION_DESCRIPTOR MiscRecord (8)
    # ULONG64 Reserved0 (8)
    # ULONG64 Reserved1 (8)
    # total = 8+4+4+4+4+52+8+8+8+8 = 108
    off = 4
    for _ in range(count):
        if off + 108 > len(blob):
            break
        base = _u64(blob, off)
        size = _u32(blob, off + 8)
        checksum = _u32(blob, off + 12)
        timestamp = _u32(blob, off + 16)
        name_rva = _u32(blob, off + 20)
        # VS_FIXEDFILEINFO at off+24
        ver = None
        if off + 24 + 52 <= len(blob) and _u32(blob, off + 24) == 0xFEEF04BD:
            ms = _u32(blob, off + 24 + 8)
            ls = _u32(blob, off + 24 + 12)
            ver = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        path = _read_utf16z(data, name_rva)
        # ModuleNameRva points to MINIDUMP_STRING: ULONG32 Length; WCHAR Buffer[]
        if name_rva and name_rva + 4 < len(data):
            slen = _u32(data, name_rva)
            try:
                path = data[name_rva + 4 : name_rva + 4 + slen].decode("utf-16-le", errors="replace").rstrip("\x00")
            except Exception:
                path = _read_utf16z(data, name_rva + 4)
        name = Path(path).name if path else f"module_{base:08X}"
        modules.append(
            ModuleInfo(
                name=name,
                base=base,
                size=size,
                end=base + size,
                timestamp=timestamp,
                checksum=checksum,
                version=ver,
                path=path or None,
            )
        )
        off += 108
    return modules


def parse_unloaded_modules(data: bytes, blob: bytes) -> list[dict[str, Any]]:
    if len(blob) < 8:
        return []
    # ULONG32 SizeOfHeader, SizeOfEntry, NumberOfEntries
    if len(blob) < 12:
        return []
    size_header, size_entry, count = struct.unpack_from("<III", blob, 0)
    out = []
    off = size_header
    for _ in range(count):
        if off + 24 > len(blob):
            break
        base = _u64(blob, off)
        size = _u32(blob, off + 8)
        # checksum, timestamp, name rva depend on entry size
        name = ""
        if size_entry >= 24:
            name_rva = _u32(blob, off + 20) if size_entry >= 24 else 0
            # actually layout: Base 8, Size 4, CheckSum 4, TimeDateStamp 4, ModuleNameRva 4 = 24
            checksum = _u32(blob, off + 12)
            timestamp = _u32(blob, off + 16)
            name_rva = _u32(blob, off + 20)
            if name_rva and name_rva + 4 < len(data):
                slen = _u32(data, name_rva)
                name = data[name_rva + 4 : name_rva + 4 + slen].decode("utf-16-le", errors="replace").rstrip("\x00")
        out.append(
            {
                "name": Path(name).name if name else f"unloaded_{base:08X}",
                "path": name or None,
                "base": base,
                "size": size,
                "end": base + size,
            }
        )
        off += size_entry
    return out


def parse_exception(blob: bytes) -> ExceptionInfo:
    # MINIDUMP_EXCEPTION_STREAM
    # ULONG32 ThreadId
    # ULONG32 __alignment
    # MINIDUMP_EXCEPTION ExceptionRecord
    # MINIDUMP_LOCATION_DESCRIPTOR ThreadContext
    thread_id = _u32(blob, 0)
    # ExceptionRecord at offset 8
    code = _u32(blob, 8)
    flags = _u32(blob, 12)
    record = _u64(blob, 16)
    address = _u64(blob, 24)
    nparams = _u32(blob, 32)
    params = []
    for i in range(min(nparams, 15)):
        params.append(_u64(blob, 40 + i * 8))
    access_type = None
    access_address = None
    if (code & 0xFFFFFFFF) == 0xC0000005 and len(params) >= 2:
        access_type = {0: "read", 1: "write", 8: "dep_execute"}.get(params[0] & 0xFF, f"type_{params[0]}")
        access_address = params[1]
    return ExceptionInfo(
        code=code & 0xFFFFFFFF,
        code_name=_exception_name(code),
        flags=flags,
        record=record,
        address=address,
        parameters=params,
        access_type=access_type,
        access_address=access_address,
        thread_id=thread_id,
    )


def _parse_x86_context(ctx: bytes) -> dict[str, int]:
    """Parse CONTEXT_i386 (see winnt.h).

    Layout:
      0x00 ContextFlags
      0x04 Dr0..Dr7 (7 dwords) -> 0x1C
      0x1C FLOATING_SAVE_AREA (0x70 bytes) -> 0x8C
      0x8C SegGs, SegFs, SegEs, SegDs
      0x9C Edi, Esi, Ebx, Edx, Ecx, Eax
      0xB4 Ebp, Eip, SegCs, EFlags, Esp, SegSs
    """
    if len(ctx) < 0xCC:
        return {}
    flags = _u32(ctx, 0)
    return {
        "context_flags": flags,
        "edi": _u32(ctx, 0x9C),
        "esi": _u32(ctx, 0xA0),
        "ebx": _u32(ctx, 0xA4),
        "edx": _u32(ctx, 0xA8),
        "ecx": _u32(ctx, 0xAC),
        "eax": _u32(ctx, 0xB0),
        "ebp": _u32(ctx, 0xB4),
        "eip": _u32(ctx, 0xB8),
        "seg_cs": _u32(ctx, 0xBC),
        "eflags": _u32(ctx, 0xC0),
        "esp": _u32(ctx, 0xC4),
        "seg_ss": _u32(ctx, 0xC8) if len(ctx) >= 0xCC else 0,
    }


def parse_thread_list(data: bytes, blob: bytes) -> list[ThreadInfo]:
    if len(blob) < 4:
        return []
    count = _u32(blob, 0)
    threads: list[ThreadInfo] = []
    # MINIDUMP_THREAD = 48 bytes:
    # ULONG32 ThreadId
    # ULONG32 SuspendCount
    # ULONG32 PriorityClass
    # ULONG32 Priority
    # ULONG64 Teb
    # MINIDUMP_MEMORY_DESCRIPTOR Stack (16: ULONG64 Start, LOCATION DataSize+Rva)
    # MINIDUMP_LOCATION_DESCRIPTOR ThreadContext (8)
    off = 4
    for _ in range(count):
        if off + 48 > len(blob):
            break
        tid = _u32(blob, off)
        suspend = _u32(blob, off + 4)
        prio_class = _u32(blob, off + 8)
        prio = _u32(blob, off + 12)
        teb = _u64(blob, off + 16)
        stack_start = _u64(blob, off + 24)
        stack_size = _u32(blob, off + 32)
        stack_rva = _u32(blob, off + 36)
        ctx_size = _u32(blob, off + 40)
        ctx_rva = _u32(blob, off + 44)
        regs: dict[str, int] = {}
        if ctx_rva and ctx_size:
            try:
                ctx = _rva_slice(data, ctx_rva, ctx_size)
                regs = _parse_x86_context(ctx)
            except ValueError:
                regs = {}
        stack_words: list[dict[str, Any]] = []
        if stack_rva and stack_size:
            try:
                stack = _rva_slice(data, stack_rva, min(stack_size, 256))
                for i in range(0, min(len(stack), 64), 4):
                    w = _u32(stack, i)
                    stack_words.append({"offset": i, "value": w})
            except ValueError:
                pass
        threads.append(
            ThreadInfo(
                thread_id=tid,
                suspend_count=suspend,
                priority_class=prio_class,
                priority=prio,
                teb=teb,
                eip=regs.get("eip"),
                esp=regs.get("esp"),
                ebp=regs.get("ebp"),
                eax=regs.get("eax"),
                ebx=regs.get("ebx"),
                ecx=regs.get("ecx"),
                edx=regs.get("edx"),
                esi=regs.get("esi"),
                edi=regs.get("edi"),
                eflags=regs.get("eflags"),
                stack_words=stack_words,
            )
        )
        off += 48
    return threads


def find_module(modules: list[ModuleInfo], address: int) -> ModuleInfo | None:
    for m in modules:
        if m.base <= address < m.end:
            return m
    return None


def resolve_ffximain(
    modules: list[ModuleInfo],
    fault_addr: int | None,
    dll_path: Path | None,
) -> dict[str, Any] | None:
    m = None
    for cand in modules:
        n = cand.name.lower()
        if n in ("ffximain.dll", "ffximain"):
            m = cand
            break
    if m is None:
        return None
    info: dict[str, Any] = {
        "name": m.name,
        "path": m.path,
        "base": m.base,
        "size": m.size,
        "end": m.end,
        "preferred_base": FFXIMAIN_PREFERRED_BASE,
        "reloc_delta": m.base - FFXIMAIN_PREFERRED_BASE,
    }
    if fault_addr is not None and m.base <= fault_addr < m.end:
        rva = fault_addr - m.base
        info["fault_in_module"] = True
        info["fault_rva"] = rva
        info["fault_preferred_va"] = FFXIMAIN_PREFERRED_BASE + rva
    else:
        info["fault_in_module"] = False
    if dll_path and dll_path.is_file():
        info["local_dll"] = str(dll_path)
    return info


def disasm_around(
    dll_path: Path,
    preferred_va: int,
    radius: int = 64,
) -> list[dict[str, Any]]:
    """Disassemble around preferred VA in a local FFXiMain (unpacked or packed)."""
    try:
        import capstone
        import pefile as pefile_mod
    except ImportError:
        return []

    pe = pefile_mod.PE(str(dll_path), fast_load=True)
    image_base = pe.OPTIONAL_HEADER.ImageBase
    rva = preferred_va - FFXIMAIN_PREFERRED_BASE
    # If user passed a packed DLL with image base 0x10000000, same RVA works
    file_off = None
    sec_name = None
    for s in pe.sections:
        va = s.VirtualAddress
        vsz = max(s.Misc_VirtualSize, s.SizeOfRawData)
        if va <= rva < va + vsz:
            file_off = s.PointerToRawData + (rva - va)
            sec_name = s.Name.rstrip(b"\x00").decode("ascii", "replace")
            break
    if file_off is None:
        return []

    start_off = max(0, file_off - radius)
    size = radius * 2 + 16
    with open(dll_path, "rb") as f:
        f.seek(start_off)
        blob = f.read(size)
    if not blob:
        return []

    # VA for disasm start
    start_rva = rva - (file_off - start_off)
    start_va = FFXIMAIN_PREFERRED_BASE + start_rva
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = False
    out: list[dict[str, Any]] = []
    for ins in md.disasm(blob, start_va):
        out.append(
            {
                "va": ins.address,
                "rva": ins.address - FFXIMAIN_PREFERRED_BASE,
                "bytes": ins.bytes.hex(),
                "mnemonic": ins.mnemonic,
                "op_str": ins.op_str,
                "is_fault": ins.address == preferred_va,
                "section": sec_name,
            }
        )
        if ins.address > preferred_va + radius:
            break
    return out


def annotate_stack(modules: list[ModuleInfo], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for w in words:
        val = w["value"]
        mod = find_module(modules, val)
        entry = dict(w)
        entry["value_hex"] = _hex(val)
        if mod:
            entry["module"] = mod.name
            entry["module_rva"] = val - mod.base
            if mod.name.lower().startswith("ffximain"):
                entry["ffximain_preferred_va"] = FFXIMAIN_PREFERRED_BASE + (val - mod.base)
        out.append(entry)
    return out


# ── Top-level parse ──────────────────────────────────────────────────────────

def parse_minidump(
    dump_path: Path,
    ffximain_dll: Path | None = None,
    disasm_radius: int = 64,
) -> CrashReport:
    data = dump_path.read_bytes()
    hdr = parse_header(data)
    directory = parse_directory(data, hdr["stream_directory_rva"], hdr["number_of_streams"])

    streams_meta = []
    by_type: dict[int, list[tuple[int, int]]] = {}
    for st, size, rva in directory:
        streams_meta.append(
            {
                "type": st,
                "name": STREAM_NAMES.get(st, f"Type_{st}"),
                "size": size,
                "rva": rva,
            }
        )
        if size and rva:
            by_type.setdefault(st, []).append((rva, size))

    notes: list[str] = []
    system: dict[str, Any] = {}
    for rva, size in by_type.get(STREAM_SYSTEM_INFO, []):
        try:
            system = parse_system_info(_rva_slice(data, rva, size))
            csd_rva = system.get("csd_version_rva") or 0
            if csd_rva:
                slen = _u32(data, csd_rva) if csd_rva + 4 <= len(data) else 0
                if slen:
                    system["csd_version"] = (
                        data[csd_rva + 4 : csd_rva + 4 + slen]
                        .decode("utf-16-le", errors="replace")
                        .rstrip("\x00")
                    )
        except Exception as e:
            notes.append(f"SystemInfo parse error: {e}")

    for rva, size in by_type.get(STREAM_MISC_INFO, []):
        try:
            misc = parse_misc_info(_rva_slice(data, rva, size))
            system.update({f"misc_{k}": v for k, v in misc.items()})
        except Exception as e:
            notes.append(f"MiscInfo parse error: {e}")

    modules: list[ModuleInfo] = []
    for rva, size in by_type.get(STREAM_MODULE_LIST, []):
        try:
            modules = parse_module_list(data, _rva_slice(data, rva, size), rva)
        except Exception as e:
            notes.append(f"ModuleList parse error: {e}")

    unloaded: list[dict[str, Any]] = []
    for rva, size in by_type.get(STREAM_UNLOADED_MODULE_LIST, []):
        try:
            unloaded = parse_unloaded_modules(data, _rva_slice(data, rva, size))
        except Exception as e:
            notes.append(f"UnloadedModuleList parse error: {e}")

    exc: ExceptionInfo | None = None
    for rva, size in by_type.get(STREAM_EXCEPTION, []):
        try:
            exc = parse_exception(_rva_slice(data, rva, size))
        except Exception as e:
            notes.append(f"Exception parse error: {e}")

    threads: list[ThreadInfo] = []
    for rva, size in by_type.get(STREAM_THREAD_LIST, []):
        try:
            threads = parse_thread_list(data, _rva_slice(data, rva, size))
        except Exception as e:
            notes.append(f"ThreadList parse error: {e}")

    if exc and exc.thread_id is not None:
        for t in threads:
            if t.thread_id == exc.thread_id:
                t.is_exception_thread = True

    fault_addr = exc.address if exc else None
    fault_mod = None
    if fault_addr is not None:
        m = find_module(modules, fault_addr)
        if m:
            fault_mod = {
                "name": m.name,
                "path": m.path,
                "base": m.base,
                "size": m.size,
                "rva": fault_addr - m.base,
            }

    # Prefer exception-thread EIP if fault address is null
    if fault_addr is None:
        for t in threads:
            if t.is_exception_thread and t.eip:
                fault_addr = t.eip
                m = find_module(modules, t.eip)
                if m:
                    fault_mod = {
                        "name": m.name,
                        "path": m.path,
                        "base": m.base,
                        "size": m.size,
                        "rva": t.eip - m.base,
                    }
                break

    ffxi = resolve_ffximain(modules, fault_addr, ffximain_dll)

    disasm: list[dict[str, Any]] = []
    if ffxi and ffxi.get("fault_in_module") and ffximain_dll and ffximain_dll.is_file():
        try:
            disasm = disasm_around(ffximain_dll, int(ffxi["fault_preferred_va"]), disasm_radius)
        except Exception as e:
            notes.append(f"Disasm error: {e}")

    # Annotate exception thread stack
    for t in threads:
        if t.stack_words:
            t.stack_words = annotate_stack(modules, t.stack_words)

    # Heuristic notes for inventory work
    if exc and exc.code_name == "ACCESS_VIOLATION" and ffxi and ffxi.get("fault_in_module"):
        notes.append(
            "Fault inside FFXiMain — likely related to client patches "
            f"(preferred VA { _hex(ffxi.get('fault_preferred_va')) }, "
            f"RVA { _hex(ffxi.get('fault_rva')) })."
        )
    if exc and exc.access_type == "read" and exc.access_address == 0:
        notes.append("Null pointer read (access address 0).")
    if exc and exc.access_type == "write" and exc.access_address is not None:
        notes.append(f"Write AV at {_hex(exc.access_address)} — possible buffer overrun / bad pointer.")

    return CrashReport(
        dump_path=str(dump_path.resolve()),
        dump_size=len(data),
        dump_mtime=datetime.fromtimestamp(dump_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        signature=hdr["signature"],
        version=hdr["version"],
        flags=hdr["flags"],
        timestamp=hdr["timestamp"],
        timestamp_iso=_ts_to_iso(hdr["timestamp"]),
        streams=streams_meta,
        system=system,
        exception=asdict(exc) if exc else None,
        modules=[asdict(m) for m in modules],
        unloaded_modules=unloaded,
        threads=[asdict(t) for t in threads],
        fault_module=fault_mod,
        ffximain=ffxi,
        disasm=disasm,
        notes=notes,
    )


# ── Rendering ────────────────────────────────────────────────────────────────

def render_txt(report: CrashReport) -> str:
    lines: list[str] = []
    w = lines.append

    w("=" * 72)
    w("FFXI / POL crash dump overview")
    w("=" * 72)
    w(f"Dump        : {report.dump_path}")
    w(f"Size        : {report.dump_size:,} bytes")
    w(f"File mtime  : {report.dump_mtime}")
    w(f"Dump time   : {report.timestamp_iso or 'n/a'} (unix {report.timestamp})")
    w(f"Flags       : {_hex(report.flags)}")
    w("")

    if report.system:
        w("-" * 72)
        w("System")
        w("-" * 72)
        w(f"OS          : {report.system.get('os_version', 'n/a')} "
          f"({report.system.get('csd_version', '').strip()})")
        w(f"Arch        : {report.system.get('processor_architecture', 'n/a')}")
        w(f"CPUs        : {report.system.get('number_of_processors', 'n/a')}")
        if "misc_process_id" in report.system:
            w(f"Process ID  : {report.system.get('misc_process_id')}")
            w(f"Create time : {report.system.get('misc_process_create_time_iso', 'n/a')}")
        w("")

    if report.exception:
        e = report.exception
        w("-" * 72)
        w("Exception")
        w("-" * 72)
        w(f"Code        : {_hex(e['code'])}  {e['code_name']}")
        w(f"Address     : {_hex(e['address'])}")
        w(f"Thread ID   : {e.get('thread_id')}")
        if e.get("access_type") is not None:
            w(f"AV type     : {e['access_type']}")
            w(f"AV address  : {_hex(e.get('access_address'))}")
        if e.get("parameters"):
            w("Parameters  : " + ", ".join(_hex(p) for p in e["parameters"][:8]))
        w("")

    if report.fault_module:
        fm = report.fault_module
        w("-" * 72)
        w("Fault module")
        w("-" * 72)
        w(f"Module      : {fm['name']}")
        w(f"Path        : {fm.get('path') or 'n/a'}")
        w(f"Base        : {_hex(fm['base'])}")
        w(f"Size        : {_hex(fm['size'])}")
        w(f"Fault RVA   : {_hex(fm.get('rva'))}")
        w("")

    if report.ffximain:
        f = report.ffximain
        w("-" * 72)
        w("FFXiMain")
        w("-" * 72)
        w(f"Mapped base : {_hex(f['base'])}")
        w(f"Preferred   : {_hex(f['preferred_base'])}")
        w(f"Reloc delta : {_hex(f['reloc_delta'], 1) if f['reloc_delta'] else '0 (preferred)'}")
        if f.get("fault_in_module"):
            w(f"Fault RVA   : {_hex(f.get('fault_rva'))}")
            w(f"Preferred VA: {_hex(f.get('fault_preferred_va'))}  "
              f"(use this in Ghidra @ image base 0x10000000)")
        else:
            w("Fault not inside FFXiMain.")
        if f.get("local_dll"):
            w(f"Local DLL   : {f['local_dll']}")
        w("")

    # Exception thread registers
    et = next((t for t in report.threads if t.get("is_exception_thread")), None)
    if et:
        w("-" * 72)
        w(f"Exception thread  tid={et['thread_id']}")
        w("-" * 72)
        # Prefer Exception.Address as fault EIP — CONTEXT EIP is often ntdll dispatch
        fault_eip = None
        if report.exception and report.exception.get("address"):
            fault_eip = report.exception["address"]
        ctx_eip = et.get("eip")
        w(f"Fault EIP   : {_hex(fault_eip)}  (from Exception stream)")
        if ctx_eip is not None and ctx_eip != fault_eip:
            w(f"Context EIP : {_hex(ctx_eip)}  (may be exception dispatcher)")
        w(f"ESP={_hex(et.get('esp'))}  EBP={_hex(et.get('ebp'))}")
        w(f"EAX={_hex(et.get('eax'))}  EBX={_hex(et.get('ebx'))}  ECX={_hex(et.get('ecx'))}  EDX={_hex(et.get('edx'))}")
        w(f"ESI={_hex(et.get('esi'))}  EDI={_hex(et.get('edi'))}  EFLAGS={_hex(et.get('eflags'))}")
        if et.get("stack_words"):
            w("")
            w("Stack (first dwords @ ESP dump):")
            for sw in et["stack_words"][:16]:
                extra = ""
                if sw.get("module"):
                    extra = f"  -> {sw['module']}+{_hex(sw.get('module_rva'))}"
                    if sw.get("ffximain_preferred_va"):
                        extra += f"  (FFXiMain {_hex(sw['ffximain_preferred_va'])})"
                w(f"  +{sw['offset']:02X}  {_hex(sw['value'])}{extra}")
        w("")

    if report.disasm:
        w("-" * 72)
        w("Disassembly near fault (FFXiMain preferred VAs)")
        w("-" * 72)
        for ins in report.disasm:
            mark = ">>>" if ins.get("is_fault") else "   "
            w(
                f"{mark} {_hex(ins['va'])}  {ins['bytes']:<20}  "
                f"{ins['mnemonic']} {ins['op_str']}"
            )
        w("")

    w("-" * 72)
    w(f"Modules ({len(report.modules)})")
    w("-" * 72)
    # Highlight FFXI-related first
    def sort_key(m: dict[str, Any]) -> tuple:
        n = (m.get("name") or "").lower()
        pri = 0 if n.startswith(("ffxi", "pol", "ashita", "windower")) else 1
        return (pri, m.get("base") or 0)

    for m in sorted(report.modules, key=sort_key):
        w(
            f"  {_hex(m['base'])}-{_hex(m['end'])}  "
            f"{m['name']:<28}  {m.get('version') or '':12}  "
            f"{m.get('path') or ''}"
        )
    w("")

    if report.unloaded_modules:
        w("-" * 72)
        w(f"Unloaded modules ({len(report.unloaded_modules)})")
        w("-" * 72)
        for m in report.unloaded_modules:
            w(f"  {_hex(m['base'])}-{_hex(m['end'])}  {m['name']}")
        w("")

    w("-" * 72)
    w(f"Threads ({len(report.threads)})")
    w("-" * 72)
    for t in report.threads:
        mark = " *" if t.get("is_exception_thread") else "  "
        w(
            f"{mark} tid={t['thread_id']:<6}  "
            f"EIP={_hex(t.get('eip'))}  ESP={_hex(t.get('esp'))}  "
            f"suspend={t.get('suspend_count')}"
        )
    w("")

    if report.notes:
        w("-" * 72)
        w("Notes")
        w("-" * 72)
        for n in report.notes:
            w(f"  - {n}")
        w("")

    w("-" * 72)
    w("Streams")
    w("-" * 72)
    for s in report.streams:
        if s["size"] == 0 and s["type"] == 0:
            continue
        w(f"  {s['name']:<22} type={s['type']:<4} size={s['size']:<8} rva={_hex(s['rva'])}")
    w("")
    w("=" * 72)
    return "\n".join(lines) + "\n"


def _json_ready(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, int) and abs(obj) > 0xFFFFFFFF:
        # keep large ints as hex strings for readability? keep as int (JSON ok)
        return obj
    return obj


def report_to_json(report: CrashReport) -> dict[str, Any]:
    d = asdict(report)
    # Add hex convenience fields on exception
    if d.get("exception"):
        e = d["exception"]
        e["code_hex"] = _hex(e["code"])
        e["address_hex"] = _hex(e["address"])
        if e.get("access_address") is not None:
            e["access_address_hex"] = _hex(e["access_address"])
    if d.get("ffximain"):
        f = d["ffximain"]
        for k in ("base", "size", "end", "preferred_base", "reloc_delta", "fault_rva", "fault_preferred_va"):
            if f.get(k) is not None:
                f[f"{k}_hex"] = _hex(f[k])
    return _json_ready(d)


# ── Dump discovery ───────────────────────────────────────────────────────────

def find_dumps(dump_dir: Path, pattern: str = "*.dmp") -> list[Path]:
    if not dump_dir.is_dir():
        return []
    return sorted(dump_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)


def pick_default_dump(dump_dir: Path) -> Path | None:
    dumps = find_dumps(dump_dir)
    if not dumps:
        return None
    # Prefer pol / ffxi related
    for p in dumps:
        n = p.name.lower()
        if n.startswith(("pol.exe", "ffxi", "ffximain")):
            return p
    return dumps[0]


def default_ffximain_candidates() -> list[Path]:
    roots = [
        Path(XI_TOOLS_DIR) / "ghidra" / "FFXiMain" / "patch120" / "unpacked_for_patch.dll",
        Path(XI_TOOLS_DIR) / "ghidra" / "FFXiMain" / "FFXiMain_unpacked.dll",
        Path(XI_TOOLS_DIR) / "misc" / "FFXiMain_unpacked.dll",
        Path(r"D:\cexi\catseyexi-client\Game\FINAL FANTASY XI\FFXiMain.dll"),
    ]
    return [p for p in roots if p.is_file()]


# ── CLI ──────────────────────────────────────────────────────────────────────

@click.command("crashdump")
@click.argument(
    "dump",
    required=False,
    type=click.Path(path_type=Path),
)
@click.option(
    "--dir",
    "dump_dir",
    type=click.Path(path_type=Path),
    default=None,
    help=f"CrashDumps folder [default: %LOCALAPPDATA%\\CrashDumps]",
)
@click.option(
    "--latest/--no-latest",
    default=True,
    show_default=True,
    help="If no dump path given, use newest pol/ffxi dump in --dir.",
)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    help="List dumps in --dir and exit.",
)
@click.option(
    "--ffximain",
    "ffximain_dll",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Local FFXiMain.dll (unpacked preferred) for disassembly near fault.",
)
@click.option(
    "--disasm-radius",
    default=64,
    show_default=True,
    type=int,
    help="Bytes of context each side of fault for disasm.",
)
@click.option(
    "--json-only",
    is_flag=True,
    help="Write only .json (skip .txt).",
)
@click.option(
    "--txt-only",
    is_flag=True,
    help="Write only .txt (skip .json).",
)
@click.option(
    "--stdout",
    "to_stdout",
    is_flag=True,
    help="Also print the text overview to stdout.",
)
def cmd(
    dump: Path | None,
    dump_dir: Path | None,
    latest: bool,
    list_only: bool,
    ffximain_dll: Path | None,
    disasm_radius: int,
    json_only: bool,
    txt_only: bool,
    to_stdout: bool,
) -> None:
    """Parse a Windows minidump and write overview next to the dump file.

    \b
    Examples:
      xi dll ffximain crashdump
      xi dll ffximain crashdump --list
      xi dll ffximain crashdump path/to/pol.exe.1234.dmp
      xi dll ffximain crashdump --ffximain D:/xi-tools/ghidra/FFXiMain/FFXiMain_unpacked.dll
    """
    dump_dir = dump_dir or DEFAULT_DUMP_DIR

    if list_only:
        dumps = find_dumps(dump_dir)
        if not dumps:
            click.echo(f"No .dmp files in {dump_dir}")
            return
        click.echo(f"Dumps in {dump_dir} ({len(dumps)}):\n")
        for p in dumps[:50]:
            st = p.stat()
            ts = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            click.echo(f"  {ts}  {st.st_size:10,}  {p.name}")
        if len(dumps) > 50:
            click.echo(f"  ... and {len(dumps) - 50} more")
        return

    if dump is None:
        if not latest:
            raise click.UsageError("Provide a dump path or use --latest")
        dump = pick_default_dump(dump_dir)
        if dump is None:
            raise click.ClickException(f"No dump files found in {dump_dir}")
        click.echo(f"Using latest dump: {dump}")

    dump = dump.expanduser().resolve()
    if not dump.is_file():
        raise click.ClickException(f"Dump not found: {dump}")

    if ffximain_dll is None:
        cands = default_ffximain_candidates()
        ffximain_dll = cands[0] if cands else None
        if ffximain_dll:
            click.echo(f"FFXiMain DLL   : {ffximain_dll}")

    click.echo(f"Parsing        : {dump} ({dump.stat().st_size:,} bytes)")
    t0 = time.perf_counter()
    report = parse_minidump(dump, ffximain_dll=ffximain_dll, disasm_radius=disasm_radius)
    dt = time.perf_counter() - t0
    click.echo(f"Parsed in      : {dt:.2f}s")

    stem = dump.with_suffix("")  # pol.exe.1234
    txt_path = Path(str(stem) + ".crash.txt")
    json_path = Path(str(stem) + ".crash.json")

    if not json_only:
        txt = render_txt(report)
        txt_path.write_text(txt, encoding="utf-8")
        click.echo(f"Wrote          : {txt_path}")
        if to_stdout:
            click.echo("")
            click.echo(txt)

    if not txt_only:
        payload = report_to_json(report)
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        click.echo(f"Wrote          : {json_path}")

    # One-line summary
    if report.exception:
        e = report.exception
        loc = ""
        if report.ffximain and report.ffximain.get("fault_in_module"):
            loc = f"  FFXiMain {_hex(report.ffximain.get('fault_preferred_va'))}"
        elif report.fault_module:
            loc = f"  {report.fault_module['name']}+{_hex(report.fault_module.get('rva'))}"
        click.echo(
            f"Summary        : {e['code_name']} @ {_hex(e['address'])}{loc}"
        )
