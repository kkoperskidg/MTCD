import sys
import os.path
from setuptools import setup, find_packages

open_kwds = {}
if sys.version_info > (3,):
    open_kwds['encoding'] = 'utf-8'

req_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "requirements.txt")
with open(req_path) as f:
    requires = f.read().splitlines()
    
# with open('README.md', **open_kwds) as f:
#     readme = f.read()

# long_description=readme,

setup(name='mtcd',
      version='1.0.2',
      description='Mutltitemporal Change Detection Workflow MTCD.',
      classifiers=[],
      keywords='',
      author='DigitalGlobe',
      author_email='',
      license='MIT',
      packages=find_packages(exclude=['docs','tests']),
      include_package_data=True,
      zip_safe=False,
      install_requires=requires,
      setup_requires=[],
      tests_require=[]
      )
