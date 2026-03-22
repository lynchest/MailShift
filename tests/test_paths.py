import pytest
from pathlib import Path
from mailshift.utils.paths import get_path, ROOT_DIR

def test_get_path_resolves_correctly():
    """Test get_path returns a path relative to the project root."""
    relative = "config.yaml"
    result = get_path(relative)

    assert isinstance(result, Path)
    assert result.is_absolute()
    expected = ROOT_DIR / relative
    assert result == expected

def test_get_path_with_nested_directory():
    """Test get_path works with nested directories."""
    relative = "nested/directory/file.txt"
    result = get_path(relative)

    expected = ROOT_DIR / relative
    assert result == expected
    assert result.parts[-3:] == ('nested', 'directory', 'file.txt')

def test_root_dir_is_absolute():
    """Test ROOT_DIR is an absolute path and points to the project root."""
    assert isinstance(ROOT_DIR, Path)
    assert ROOT_DIR.is_absolute()

    # Check if src and tests directories exist relative to ROOT_DIR
    assert (ROOT_DIR / "src").is_dir()
    assert (ROOT_DIR / "tests").is_dir()

    # Check if this file is correctly located under ROOT_DIR
    assert (ROOT_DIR / "tests" / "test_paths.py").is_file()
