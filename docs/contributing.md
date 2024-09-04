# Contributor Guide

## Conda environment

You can install and activate the following conda environment:
```
cd C-Star
conda env create -f ci/environment.yml
conda activate cstar_env
```

This conda environment is useful for any of the following steps:

1. Running the example notebooks
2. Contributing code and running the testing suite
3. Building the documentation locally


## Running the tests
You can check the functionality of the C-Star code by running the test suite:
```
conda activate cstar_env
cd C-Star
pytest
```

## Contributing code
If you have written new code, you can run the tests as described in the previous step. You will likely have to iterate here several times until all tests pass. The next step is to make sure that the code is formatted properly. Activate the environment:
```
conda activate cstar_env
```
You can now run all the linters with:
```
pre-commit run --all-files
```
Some things will automatically be reformatted, others need manual fixes. Follow the instructions in the terminal until all checks pass. Once you got everything to pass, you can stage and commit your changes and push them to the remote github repository.

## Building the documentation locally

Activate the environment:

```
conda activate cstar_env
```
Then navigate to the docs folder and build the docs via:
```
cd docs
make fresh
make html
```
You can now open `docs/_build/html/index.html` in a web browser via
```
open _build/html/index.html
```