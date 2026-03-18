import os
import re

def repair_content(content):
    # Step 1: Remove the specific syntax error in history.py
    content = content.replace('\n|\n', '\n')
    
    # Step 2: Repair double-encoding mangling
    try:
        # Try to repair the string if it was double-encoded
        # (interpreted as latin-1/cp1252 and then saved as utf-8)
        repaired = content.encode('cp1252').decode('utf-8')
        content = repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Fallback to dictionary replacement for common mangled sequences
        replacements = {
            'Ã¼': 'ü', 'Ã¶': 'ö', 'Ã§': 'ç', 'ÅŸ': 'ş', 'ÄŸ': 'ğ', 'Ä±': 'ı',
            'Ãœ': 'Ü', 'Ã–': 'Ö', 'Ã‡': 'Ç', 'Åž': 'Ş', 'Äž': 'Ğ', 'Ä°': 'İ',
            'â€¦': '…', 'âœ“': '✔', 'âœ—': '✘', 'âš ': '⚠', 'âš¡': '⚡',
            'â”€': '─', 'â”‚': '│', 'â”œ': '├', 'â”¤': '┤', 'â”¬': '┬', 'â”´': '┴', 'â”¼': '┼',
            'Ã¢': 'â', 'Ä…': 'ą', 'Ä‡': 'ć', 'Ä™': 'ę', 'Å‚': 'ł', 'Å„': 'ń', 'Ã³': 'ó', 'Å›': 'ś', 'Åº': 'ź', 'Å¼': 'ż'
        }
        for mangled, fixed in replacements.items():
            content = content.replace(mangled, fixed)

    # Step 3: Remove excessive blank lines (3+ to 2)
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    return content

def fix_file(path):
    if not os.path.exists(path):
        return
        
    print(f"Repairing {path}...")
    with open(path, 'rb') as f:
        raw = f.read()

    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        try:
            content = raw.decode('cp1252')
        except:
            print(f"Failed to decode {path}")
            return

    repaired = repair_content(content)
    
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(repaired)

# List of files to repair based on the user's recent actions
files_to_fix = [
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\main.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\config\config.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\core\engine.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\core\analyzers\fast.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\core\analyzers\pro.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\core\analyzers\base.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\ui\cli.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\utils\history.py",
    r"c:\Users\erens\Desktop\MailShift\src\mailshift\utils\paths.py",
]

for f in files_to_fix:
    fix_file(f)
