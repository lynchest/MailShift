import timeit

setup = """
name = "SomeFolder"
keywords = ("trash", "bin", "deleted", "çöp", "silinmiş", "papelera", "corbeille", "papierkorb")
"""

old_code = """
has_keyword = any(k in name.lower() for k in keywords)
"""

new_code = """
lower_name = name.lower()
has_keyword = any(k in lower_name for k in keywords)
"""

n = 1000000

old_time = timeit.timeit(old_code, setup=setup, number=n)
new_time = timeit.timeit(new_code, setup=setup, number=n)

print(f"Old approach: {old_time:.4f}s")
print(f"New approach: {new_time:.4f}s")
print(f"Improvement: {((old_time - new_time) / old_time) * 100:.2f}%")
