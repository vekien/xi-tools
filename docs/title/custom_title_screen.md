# --dest-tl	LEFT , TOP
# --dest-br	RIGHT , BOTTOM

set DAT50=D:\cexi\catseyexi-client\Ashita\polplugins\DATs\catseyexi\ROM\119\50.dat
set DAT51=D:\cexi\catseyexi-client\Ashita\polplugins\DATs\catseyexi\ROM\119\51.dat

# Export
# xi ui tex sx %DAT50%

# textures (HD panels)
xi ui tex si %DAT50% --hd-only 20logo,ex1us

# terms and conditions logo
xi title sprite %DAT50% --owner titlwin --index 11 --dest-tl 250,80 --dest-br 393,190
xi title sprite %DAT50% --owner titlwin --index 22 --dest-tl 250,80 --dest-br 393,190

# main logo titlwin (75%) — both dual copies
xi title sprite %DAT50% --owner titlwin --index 0 --dest-tl 259,100 --dest-br 757,370
xi title sprite %DAT50% --owner titlwin --index 5 --dest-tl 259,100 --dest-br 757,370

# left panel
xi title sprite %DAT50% --owner 20logo --index 0 --src-xy 0,0 --src-wh 512,1024 --dest-tl 0,0 --dest-br 256,576

# right panel
xi title sprite %DAT50% --owner ex1us --index 0 --src-xy 0,0 --src-wh 512,1024 --dest-tl 768,50 --dest-br 1024,562

# hide other expansion rows
xi title sprite %DAT50% --owner ex1us --index 1 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex1us --index 2 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex1us --index 3 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex1us --index 4 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex2us --index 0 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex2us --index 1 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex2us --index 2 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex2us --index 3 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex2us --index 4 --dest-tl 2000,2000 --dest-br 2001,2001
xi title sprite %DAT50% --owner ex5us --index 0 --dest-tl 2000,2000 --dest-br 2001,2001

# copyright
xi title sprite %DAT50% --owner titlwin --index 2 --dest-tl 445,708 --dest-br 580,722

# wardrobe 3-8 badges — icons AND digits (sprite --owner wardrb only reaches the icons;
# the digits are owned by `font`, which is not a texture in this DAT)
xi title wardrobe %DAT50% --hide

# main menu loby2win (UiMenu)
xi title menu %DAT50% --menu loby --elem 0 --x 436 --y 500 --w 143 --h 24 --nav-up 4 --nav-down 3 --nav-left 1 --nav-right 1
xi title menu %DAT50% --menu loby --elem 1 --x -999 --y -999 --w 143 --h 24 --isolate
xi title menu %DAT50% --menu loby --elem 2 --x 436 --y 530 --w 143 --h 24 --nav-up 1 --nav-down 5 --nav-left 3 --nav-right 3
xi title menu %DAT50% --menu loby --elem 3 --x 436 --y 590 --w 143 --h 24 --nav-up 5 --nav-down 1 --nav-left 4 --nav-right 4
xi title menu %DAT50% --menu loby --elem 4 --x 436 --y 560 --w 143 --h 24 --nav-up 3 --nav-down 4 --nav-left 5 --nav-right 5

# Move the config menu
xi title menu %DAT50% --menu lob3 --x 20 --y 20 --w 360 --h 220 --nav-up 0 --nav-down 0 --nav-left 0 --nav-right 0
xi title menu %DAT50% --menu lob4 --x 20 --y 20 --w 360 --h 220 --nav-up 0 --nav-down 0 --nav-left 0 --nav-right 0

# Resize the character select icon
393, 190
xi title sprite %DAT50% --offset 0x23065a --dest-tl 30,30 --dest-br 456,300

# Export
# xi ui tex sx %DAT51%

# Import modified
xi ui tex si %DAT51%