test:
	python -m pytest tests/

fix:
	autopep8 --in-place -r -a schwab
	#autopep8 --in-place -r -a tests
	#autopep8 --in-place -r -a examples

coverage:
	python3 -m coverage run --source=schwab -m pytest tests/
	python3 -m coverage html

dist: clean
	python3 -m build

release: clean test dist
	python3 -m twine upload dist/*

clean:
	rm -rf build dist docs-build schwab_py.egg-info __pycache__ htmlcov
