# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Noxfile used with nox-automation to run tests across all samples.

Use nox -l to see all possible sessions.

In general, you'll want to run:

    nox -s lint
    # or
    nox -s list -- /path/to/sample/dir

And:

    nox -s tests -- /path/to/sample/dir

"""

import fnmatch
import itertools
import os
import subprocess
import tempfile

import nox

# Location of our common testing utilities. This isn't published to PyPI.
REPO_TOOLS_REQ =\
    'git+https://github.com/GoogleCloudPlatform/python-repo-tools.git'

# Arguments used for every invocation of py.test.
COMMON_PYTEST_ARGS = [
    '-x', '--no-success-flaky-report', '--cov', '--cov-config',
    '.coveragerc', '--cov-append', '--cov-report=']

# Blacklists of samples to ingnore.
# Bigtable and Speech are disabled because they use gRPC, which does not yet
# support Python 3. See: https://github.com/grpc/grpc/issues/282
TESTS_BLACKLIST = set((
    './appengine/standard',
    './bigtable',
    './speech',
    './testing'))
APPENGINE_BLACKLIST = set()


def list_files(folder, pattern):
    """Lists all files below the given folder that match the pattern."""
    for root, folders, files in os.walk(folder):
        for filename in files:
            if fnmatch.fnmatch(filename, pattern):
                yield os.path.join(root, filename)


def collect_sample_dirs(start_dir, blacklist=set()):
    """Recursively collects a list of dirs that contain tests.

    This works by listing the contents of directories and finding
    directories that have `*_test.py` files.
    """
    # Collect all the directories that have tests in them.
    for parent, subdirs, files in os.walk(start_dir):
        if any(f for f in files if f[-8:] == '_test.py'):
            # Don't recurse further, since py.test will do that.
            del subdirs[:]
            # This dir has tests in it. yield it.
            yield parent
        else:
            # Filter out dirs we don't want to recurse into
            subdirs[:] = [s for s in subdirs
                          if s[0].isalpha() and
                          os.path.join(parent, s) not in blacklist]


def get_changed_files():
    """Uses travis environment variables to determine which files
    have changed for this pull request / push."""
    # Debug info
    print('TRAVIS_PULL_REQUEST: {}'.format(
        os.environ.get('TRAVIS_PULL_REQUEST')))
    print('TRAVIS_COMMIT: {}'.format(os.environ.get('TRAVIS_COMMIT')))
    print('TRAVIS_BRANCH: {}'.format(os.environ.get('TRAVIS_BRANCH')))

    pr = os.environ.get('TRAVIS_PULL_REQUEST')
    if pr == 'false':
        # This is not a pull request.
        changed = subprocess.check_output(
            ['git', 'show', '--pretty=format:', '--name-only',
                os.environ.get('TRAVIS_COMMIT')])
    elif pr is not None:
        changed = subprocess.check_output(
            ['git', 'diff', '--name-only',
                os.environ.get('TRAVIS_COMMIT'),
                os.environ.get('TRAVIS_BRANCH')])
    else:
        changed = ''
        print('Uh... where are we?')
    return set([x for x in changed.split('\n') if x])


def filter_samples(sample_dirs, changed_files):
    """Filers the list of sample directories to only include directories that
    contain changed files."""
    result = []
    for sample_dir in sample_dirs:
        if sample_dir.startswith('./'):
            sample_dir = sample_dir[2:]
        for changed_file in changed_files:
            if changed_file.startswith(sample_dir):
                result.append(sample_dir)

    return list(set(result))


def setup_appengine(session):
    """Installs the App Engine SDK."""
    # Install the app engine sdk and setup import paths.
    gae_root = os.environ.get('GAE_ROOT', tempfile.gettempdir())
    session.env['PYTHONPATH'] = os.path.join(gae_root, 'google_appengine')
    session.run('gcprepotools', 'download-appengine-sdk', gae_root)

    # Create a lib directory to prevent the GAE vendor library from
    # complaining.
    if not os.path.exists('lib'):
        os.makedirs('lib')


def run_tests_in_sesssion(
        session, interpreter, use_appengine=False, skip_flaky=False,
        changed_only=False, sample_directories=None):
    """This is the main function for executing tests.

    It:
    1. Install the common testing utilities.
    2. Installs the test requirements for the current interpreter.
    3. Determines which pytest arguments to use. skip_flaky causes extra
       arguments to be passed that will skip tests marked flaky.
    4. If posargs are specified, it will use that as the list of samples to
       test.
    5. If posargs is not specified, it will gather the list of samples by
       walking the repository tree.
    6. If changed_only was specified, it'll use Travis environment variables
       to figure out which samples should be tested based on which files
       were changed.
    7. For each sample directory, it runs py.test.
    """
    session.interpreter = interpreter
    session.install(REPO_TOOLS_REQ)
    session.install('-r', 'requirements-{}-dev.txt'.format(interpreter))

    if use_appengine:
        setup_appengine(session)

    pytest_args = COMMON_PYTEST_ARGS[:]

    if skip_flaky:
        pytest_args.append('-m not slow and not flaky')

    # session.posargs is any leftover arguments from the command line,
    # which allows users to run a particular test instead of all of them.
    if session.posargs:
        sample_directories = session.posargs
    elif sample_directories is None:
        sample_directories = collect_sample_dirs('.', TESTS_BLACKLIST)

    if changed_only:
        changed_files = get_changed_files()
        sample_directories = filter_samples(
            sample_directories, changed_files)
        print('Running tests on a subset of samples: ')
        print('\n'.join(sample_directories))

    for sample in sample_directories:
        # Install additional dependencies if they exist
        dirname = sample if os.path.isdir(sample) else os.path.dirname(sample)
        for reqfile in list_files(dirname, 'requirements*.txt'):
            session.install('-r', reqfile)

        # Ignore lib and env directories
        ignore_args = [
            '--ignore', os.path.join(sample, 'lib'),
            '--ignore', os.path.join(sample, 'env')]

        session.run(
            'py.test', sample,
            *(pytest_args + ignore_args),
            success_codes=[0, 5])  # Treat no test collected as success.


@nox.parametrize('interpreter', ['python2.7', 'python3.4'])
def session_tests(session, interpreter):
    """Runs tests"""
    run_tests_in_sesssion(session, interpreter)


def session_gae(session):
    """Runs test for GAE Standard samples."""
    run_tests_in_sesssion(
        session, 'python2.7', use_appengine=True,
        sample_directories=collect_sample_dirs(
            'appengine/standard',
            APPENGINE_BLACKLIST))


def session_grpc(session):
    """Runs tests for samples that need grpc."""
    # TODO: Remove this when grpc supports Python 3.
    run_tests_in_sesssion(
        session,
        'python2.7',
        sample_directories=itertools.chain(
            collect_sample_dirs('speech'),
            collect_sample_dirs('bigtable')))


@nox.parametrize('subsession', ['gae', 'tests'])
def session_travis(session, subsession):
    """On travis, just run with python3.4 and don't run slow or flaky tests."""
    if subsession == 'tests':
        run_tests_in_sesssion(
            session, 'python3.4', skip_flaky=True, changed_only=True)
    else:
        run_tests_in_sesssion(
            session, 'python2.7', use_appengine=True, skip_flaky=True,
            changed_only=True,
            sample_directories=collect_sample_dirs(
                'appengine/standard',
                APPENGINE_BLACKLIST))


def session_lint(session):
    """Lints each sample."""
    session.install('flake8', 'flake8-import-order')
    session.run(
        'flake8', '--builtin=gettext', '--max-complexity=15',
        '--import-order-style=google',
        '--exclude',
        'container_engine/django_tutorial/polls/migrations/*,.nox,.cache,env,'
        'lib',
        *(session.posargs or ['.']))


def session_reqcheck(session):
    """Checks for out of date requirements."""
    session.install(REPO_TOOLS_REQ)

    if 'update' in session.posargs:
        command = 'update-requirements'
    else:
        command = 'check-requirements'

    for reqfile in list_files('.', 'requirements*.txt'):
        session.run('gcprepotools', command, reqfile)
