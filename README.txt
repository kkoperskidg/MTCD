python setup.py sdist bdist_wheel


pip install C:\Software\DGLW\dgimp\docker\MTCDworkflow\mtcd\dist\mtcd-1.0.1.tar.gz

twine upload --repository-url https://test.pypi.org/legacy/ dist/*
