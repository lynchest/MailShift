import sys
import unittest
from unittest.mock import patch, MagicMock
import shutil
import types

# We want to test the actual function from src/mailshift/ui/cli.py.
# In this environment, a full import of cli.py fails due to many missing dependencies.
# To solve this without re-implementing the function logic in the test,
# we load the module's code and execute only what we need in a controlled namespace.

def load_check_ollama_installed():
    import os
    src_path = os.path.join('src', 'mailshift', 'ui', 'cli.py')
    with open(src_path, 'r', encoding='utf-8') as f:
        code = f.read()

    # Create a namespace with the required dependencies for the function
    namespace = {'shutil': shutil}

    # Execute the specific function definition from the source code
    import re
    # We look for the exact function definition in the source file
    pattern = r'def check_ollama_installed\(\) -> bool:.*?is not None'
    match = re.search(pattern, code, re.DOTALL)

    if not match:
        raise ImportError("Could not find check_ollama_installed in src/mailshift/ui/cli.py")

    exec(match.group(0), namespace)
    return namespace['check_ollama_installed']

# Get the actual implementation from source
check_ollama_installed = load_check_ollama_installed()

class TestOllamaCheck(unittest.TestCase):
    """Tests for Ollama installation check in CLI."""

    @patch('shutil.which')
    def test_check_ollama_installed_true(self, mock_which):
        """Test that check_ollama_installed returns True when ollama is found."""
        mock_which.return_value = '/usr/local/bin/ollama'
        self.assertTrue(check_ollama_installed())
        mock_which.assert_called_once_with('ollama')

    @patch('shutil.which')
    def test_check_ollama_installed_false(self, mock_which):
        """Test that check_ollama_installed returns False when ollama is not found."""
        mock_which.return_value = None
        self.assertFalse(check_ollama_installed())
        mock_which.assert_called_once_with('ollama')

if __name__ == '__main__':
    unittest.main()
