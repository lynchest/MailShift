import pytest
from pathlib import Path
from mailshift.utils.paths import get_path, ROOT_DIR


def test_root_dir_is_absolute():
    """Verify that ROOT_DIR is an absolute Path object."""
    assert isinstance(ROOT_DIR, Path)
    assert ROOT_DIR.is_absolute()


def test_root_dir_correctness():
    """Verify that ROOT_DIR points to the actual project root directory."""
    # The project root should contain specific files/directories like 'src' and 'tests'
    assert (ROOT_DIR / "src").is_dir()
    assert (ROOT_DIR / "tests").is_dir()
    assert (ROOT_DIR / "requirements.txt").is_file()


def test_get_path_with_filename():
    """Verify get_path works correctly with a simple filename."""
    relative_path = "test_file.txt"
    expected_path = ROOT_DIR / relative_path

    result = get_path(relative_path)

    assert isinstance(result, Path)
    assert result.is_absolute()
    assert result == expected_path


def test_get_path_with_directory_and_filename():
    """Verify get_path works correctly with a path containing directories."""
    relative_path = "some/nested/dir/file.json"
    expected_path = ROOT_DIR / "some" / "nested" / "dir" / "file.json"

    result = get_path(relative_path)

    assert result == expected_path


def test_get_path_with_empty_string():
    """Verify get_path works correctly with an empty string, returning ROOT_DIR."""
    result = get_path("")
    assert result == ROOT_DIR


def test_get_path_preserves_path_separators():
    """Verify get_path handles OS-specific path separators properly via pathlib."""
    # Using pathlib / operator naturally normalizes separators
    relative_path = "dir1/dir2"
    result = get_path(relative_path)
    assert result.name == "dir2"
    assert result.parent.name == "dir1"
