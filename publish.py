#!/usr/bin/env python3
import subprocess
import shutil
import re
from pathlib import Path

PYPROJECT = Path("pyproject.toml")

def run(cmd):
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"\nHata: '{cmd}' başarısız oldu.")
        exit(1)

def get_current_version():
    content = PYPROJECT.read_text()
    match = re.search(r'^version = "(.+)"', content, re.MULTILINE)
    return match.group(1) if match else "0.0.0"

def bump_version(version, bump_type):
    parts = version.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if bump_type == "1":
        major += 1; minor = 0; patch = 0
    elif bump_type == "2":
        minor += 1; patch = 0
    elif bump_type == "3":
        patch += 1
    return f"{major}.{minor}.{patch}"

def update_version(old, new):
    content = PYPROJECT.read_text()
    content = content.replace(f'version = "{old}"', f'version = "{new}"')
    PYPROJECT.write_text(content)

def main():
    current = get_current_version()
    print(f"\nMevcut versiyon: {current}")
    print("\nNasıl artıralım?")
    print("  1) Major  (örn: 1.0.0 → 2.0.0)  — büyük değişiklik")
    print("  2) Minor  (örn: 1.0.0 → 1.1.0)  — yeni özellik")
    print("  3) Patch  (örn: 1.0.0 → 1.0.1)  — bugfix")
    print("  4) İptal")

    choice = input("\nSeçim: ").strip()
    if choice == "4" or choice not in ("1", "2", "3"):
        print("İptal edildi.")
        return

    new_version = bump_version(current, choice)
    confirm = input(f"\n{current} → {new_version} olacak, devam? (e/h): ").strip().lower()
    if confirm != "e":
        print("İptal edildi.")
        return

    print(f"\n[1/4] Versiyon güncelleniyor: {current} → {new_version}")
    update_version(current, new_version)

    print("[2/4] Eski build dosyaları siliniyor...")
    if Path("dist").exists():
        shutil.rmtree("dist")

    print("[3/4] Build alınıyor...")
    run("hatch build")

    print("[4/4] PyPI'ya yükleniyor...")
    run("twine upload --config-file .pypirc dist/*")

    print(f"\nTamamdı! mailshift {new_version} PyPI'da.")
    print(f"https://pypi.org/project/mailshift/{new_version}/")

if __name__ == "__main__":
    main()