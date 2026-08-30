#!/usr/bin/env python3
"""
Life is Strange: Double Exposure - Native Ultrawide & Cutscene Aspect Patcher
Supports 21:9, 32:9, 32:10, 16:10, custom resolutions, and cutscene framing modes.
Compatible with Python 3.6+
"""

import sys
import os
import struct
import shutil

# 11 Verified Camera Aspect Ratio Locations
ALL_ASPECT_OFFSETS = [
    0x257BDEC, 0x23E5558, 0x23E5739, 0x23E665C, 0x43FEB0F,
    0x43FEB58, 0x43FEFD1, 0x44004BF, 0x440050B, 0x4401BBF, 0x69C8A8C
]

# 2-Offset Clean Mode: Player Exploration (0x23E665C) + Photo Table (0x69C8A8C)
# Leaves cutscenes in pristine uncropped 16:9 (Zero vertical crop)
CLEAN_OFFSETS = [0x23E665C, 0x69C8A8C]

PRESETS = {
    "1": ("5120x2160 (21:9 WUHD 4K)", 5120, 2160),
    "2": ("3440x1440 (21:9 UWQHD)", 3440, 1440),
    "3": ("2560x1080 (21:9 UWD)", 2560, 1080),
    "4": ("3840x1600 (24:10 UW)", 3840, 1600),
    "5": ("5120x1440 (32:9 Super Ultrawide)", 5120, 1440),
    "6": ("3840x1080 (32:9 Super Ultrawide)", 3840, 1080),
    "7": ("7680x2160 (32:9 Super Ultrawide)", 7680, 2160),
    "8": ("3840x1200 (32:10)", 3840, 1200),
    "9": ("Restore Original Stock (16:9)", 16, 9),
}

def find_exe():
    possible_paths = [
        "Chronos-Win64-Shipping.exe",
        os.path.join("Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
        os.path.join("..", "Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
    ]
    for p in possible_paths:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None

def patch_exe(exe_path, width, height, cutscene_mode="1"):
    backup_path = exe_path + ".original"
    if not os.path.exists(backup_path):
        shutil.copy2(exe_path, backup_path)
        print("Created original backup: {}".format(os.path.basename(backup_path)))
    
    # Always read from clean original backup to ensure pristine patch
    with open(backup_path, 'rb') as f:
        data = bytearray(f.read())

    ratio = float(width) / float(height)
    target_bytes = struct.pack('<f', ratio)
    hex_str = ' '.join('{:02X}'.format(b) for b in target_bytes)
    
    print("\nTarget Resolution: {}x{}".format(width, height))
    print("Target Aspect Ratio: {:.6f} (Hex: {})".format(ratio, hex_str))
    
    if cutscene_mode == "1":
        # Clean Mode (Recommended): 21:9 Exploration & Photos, 16:9 Uncropped Cutscenes
        for off in CLEAN_OFFSETS:
            if off + 4 <= len(data):
                data[off:off+4] = target_bytes
        print("Applied Clean Patch: 21:9 Exploration + Unskewed Photos + Uncropped 16:9 Cutscenes (0% Vertical Crop).")
    else:
        # Full Ultrawide Cutscene Mode: All 11 offsets patched
        for off in ALL_ASPECT_OFFSETS:
            if off + 4 <= len(data):
                data[off:off+4] = target_bytes
        print("Applied Full Patch: 21:9 across all 11 locations (Edge-to-edge cutscenes with 16:9 lens vertical crop).")

    with open(exe_path, 'wb') as f:
        f.write(data)

    print("Successfully updated {}!".format(os.path.basename(exe_path)))
    
    ini_path = os.path.join(os.path.dirname(exe_path), "SUWSF.ini")
    if os.path.isfile(ini_path):
        with open(ini_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        if "Enabled=true" in content:
            content = content.replace("Enabled=true", "Enabled=false")
            with open(ini_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print("Disabled conflicting SUWSF.ini patch.")

def main():
    print("=" * 60)
    print(" Life is Strange: Double Exposure - Ultrawide Patcher")
    print("=" * 60)

    exe_path = find_exe()
    if not exe_path:
        exe_path = input("Enter path to Chronos-Win64-Shipping.exe: ").strip(' "\'')

    if not os.path.isfile(exe_path):
        print("Error: Could not find file at '{}'".format(exe_path))
        sys.exit(1)

    print("Found game executable: {}\n".format(exe_path))
    print("Select target aspect ratio:")
    for k, (name, _, _) in PRESETS.items():
        print("  [{}] {}".format(k, name))
    print("  [C] Custom Resolution")

    choice = input("\nEnter choice [1-9 or C]: ").strip().upper()
    if choice in PRESETS:
        _, w, h = PRESETS[choice]
    elif choice == 'C':
        w = int(input("Enter Width (e.g. 5120): ").strip())
        h = int(input("Enter Height (e.g. 2160): ").strip())
    else:
        print("Invalid resolution option.")
        return

    if choice == "9":
        # Restore stock
        patch_exe(exe_path, 16, 9, "2")
        return

    print("\nSelect Cutscene Mode:")
    print("  [1] Recommended: Uncropped 16:9 Cutscenes (0% Vertical Crop / Director Framing)")
    print("  [2] Full Ultrawide Cutscenes (Edge-to-edge / ~20% Vertical Crop)")

    cm_choice = input("\nEnter choice [1 or 2, default 1]: ").strip()
    if cm_choice != "2": cm_choice = "1"

    patch_exe(exe_path, w, h, cm_choice)

if __name__ == '__main__':
    main()
