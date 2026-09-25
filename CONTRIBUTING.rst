=============================
Contributing to ``schwab-py``
=============================

Fixing a bug? Adding a feature? Just cleaning up for the sake of cleaning up? 
Great! No improvement is too small for me, and I'm always happy to take pull 
requests. Read this guide to learn how to set up your environment so you can 
contribute.

------------------------------
Setting up the Dev Environment
------------------------------

Dependencies are listed in ``pyproject.toml``. The ``dev`` extra adds packages
for testing, documentation generation and packaging.

Before you install anything, I highly recommend setting up a virtual environment
so you don't pollute your system installation directories:

.. code-block:: shell

  python3 -m venv .venv
  source .venv/bin/activate

Next, install the project in editable mode with its development requirements:

.. code-block:: shell

  pip install -e ".[dev]"

Finally, verify everything works by running tests:

.. code-block:: shell

  make test

At this point you can make your changes.

Note that if you are using a virtual environment and switch to a new terminal
your virtual environment will not be active in the new terminal,
and you need to run the activate command again.
If you want to disable the loaded virtual environment in the same terminal window,
use the command:

.. code-block:: shell

  deactivate

----------------------
Development Guidelines
----------------------

+++++++++++++++++
Test your changes
+++++++++++++++++

This project aims for high test coverage. All changes must be properly tested, 
and we will accept no PRs that lack appropriate unit testing. We also expect 
existing tests to pass. You can run your tests using: 

.. code-block:: shell

  make test

+++++++++++++++++++++
Keep type hints valid
+++++++++++++++++++++

All code is type annotated and checked with `mypy <https://mypy-lang.org/>`__,
which CI runs:

.. code-block:: shell

  mypy

The API methods of ``Client`` and ``AsyncClient`` are defined once, in
``schwab/client/base.py``, and their types for each client come from generated
stub files. If you add or change a client method, regenerate the stubs; a test
fails if they're out of date:

.. code-block:: shell

  python tools/generate_client_stubs.py

++++++++++++++++++
Document your code
++++++++++++++++++

Documentation is how users learn to use your code, and no feature is complete 
without a full description of how to use it. If your PR changes external-facing 
interfaces, or if it alters semantics, the changes must be thoroughly described 
in the docstrings of the affected components. If your change adds a substantial 
new module, a new section in the documentation may be justified. 

Documentation is built using `Sphinx <https://www.sphinx-doc.org/en/master/>`__:

.. code-block:: shell

  sphinx-build docs/ docs-build
