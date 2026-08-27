--dest-tl	LEFT , TOP
--dest-br	RIGHT , BOTTOM

set DAT=D:\cexi\catseyexi-client\Ashita\polplugins\DATs\catseyexi\ROM\119\50.dat

========== textures (HD panels) ==========
xi ui tex si %DAT% --hd-only 20logo,ex1us

========== terms and conditions logo ==========
xi title sprite %DAT% --owner titlwin --index 11 --dest-tl 250,80 --dest-br 393,170
xi title sprite %DAT% --owner titlwin --index 22 --dest-tl 250,80 --dest-br 393,170

========== main logo titlwin (75%) — both dual copies ==========
xi title sprite %DAT% --owner titlwin --index 0 --dest-tl 259,100 --dest-br 757,360
xi title sprite %DAT% --owner titlwin --index 5 --dest-tl 259,100 --dest-br 757,360

========== left panel - 20logo ==========
xi title sprite %DAT% --owner 20logo --index 0 --src-xy 0,0 --src-wh 512,1024 --dest-tl 0,0 --dest-br 256,512

========== right panel - ex1us[0] (hijacked expansion art) ==========
xi title sprite %DAT% --owner ex1us --index 0 --src-xy 0,0 --src-wh 512,1024 --dest-tl 768,0 --dest-br 1024,512

========== hide other expansion rows ==========
xi title sprite %DAT% --owner ex1us --index 1 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex1us --index 2 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex1us --index 3 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex1us --index 4 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex2us --index 0 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex2us --index 1 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex2us --index 2 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex2us --index 3 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex2us --index 4 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT% --owner ex5us --index 0 --dest-tl 2000,2000 --dest-br 2001,2001

========== copyright ==========
xi title sprite %DAT% --owner titlwin --index 2 --dest-tl 445,708 --dest-br 580,722

========== wardrobe icons — zero dest (not drawn) ==========
xi title sprite %DAT% --owner wardrb --index 0 --hide
xi title sprite %DAT% --owner wardrb --index 1 --hide
xi title sprite %DAT% --owner wardrb --index 2 --hide
xi title sprite %DAT% --owner wardrb --index 3 --hide
xi title sprite %DAT% --owner wardrb --index 4 --hide
xi title sprite %DAT% --owner wardrb --index 5 --hide

========== main menu loby2win (UiMenu) ==========
xi title menu %DAT% --menu loby --elem 0 --x 436 --y 500 --w 143 --h 24 --nav-up 4 --nav-down 3 --nav-left 1 --nav-right 1
xi title menu %DAT% --menu loby --elem 1 --x -999 --y -999 --w 143 --h 24 --isolate
xi title menu %DAT% --menu loby --elem 2 --x 436 --y 530 --w 143 --h 24 --nav-up 1 --nav-down 5 --nav-left 3 --nav-right 3
xi title menu %DAT% --menu loby --elem 3 --x 436 --y 590 --w 143 --h 24 --nav-up 5 --nav-down 1 --nav-left 4 --nav-right 4
xi title menu %DAT% --menu loby --elem 4 --x 436 --y 560 --w 143 --h 24 --nav-up 3 --nav-down 4 --nav-left 5 --nav-right 5