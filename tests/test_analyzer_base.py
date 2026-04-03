from mailshift.core.analyzers import base
from mailshift.core.analyzers.fast import fast_analyze
from mailshift.core.analyzers.pro import pro_analyze


def test_base_re_exports_work_with_package_relative_imports() -> None:
    assert base.fast_analyze is fast_analyze
    assert base.pro_analyze is pro_analyze
