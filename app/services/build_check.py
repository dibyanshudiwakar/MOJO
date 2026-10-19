"""Build the generated service and start it, so "generated" means "runs".

The quality gate used to say ``COMPILE_AND_UNIT_TEST: NOT_EXECUTED`` unless a
caller passed ``execute_build``, so a project that did not compile still came
out green and failed afterwards, on someone's machine. This runs the skill's own
hand-off command (``handoff-build-and-reconcile.md``: ``mvn -f <parent>/pom.xml
clean verify``) and then starts the packaged jar and waits for the service to
answer, which is the other half of "build and start".

Two things keep that safe and useful:

* The environment handed to Maven and to the service is an allow-list. Generated
  code is untrusted: a plugin, a test or the service itself runs with whatever
  it is given, so the platform's own tokens are not given. Anything a build
  legitimately needs (an Artifactory user for ``settings.xml``) is named in
  ``FACTORY_BUILD_ENV_PASSTHROUGH``.
* When Maven or Java is not installed the check reports ``NOT_EXECUTED`` with
  the reason, rather than failing a project for the machine it was checked on.

The evidence carries the compiler's own error lines, not just a hash, because
the feedback agent repairs from them.
"""
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.repository import now, digest

#: ``on`` always runs, ``off`` never does, ``auto`` runs when Maven and Java exist.
MODE_ENV = 'FACTORY_BUILD'
PASSTHROUGH_ENV = 'FACTORY_BUILD_ENV_PASSTHROUGH'
BUILD_TIMEOUT = 900
START_TIMEOUT = 120
MAX_ERROR_LINES = 40
MAX_LOG_LINES = 40

_BASE_ENV = ('PATH', 'HOME', 'USERPROFILE', 'JAVA_HOME', 'M2_HOME', 'MAVEN_HOME', 'MAVEN_OPTS',
             'MAVEN_ARGS', 'SystemRoot', 'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'HOMEDRIVE',
             'HOMEPATH', 'COMSPEC', 'PATHEXT', 'LANG')
_ERROR = re.compile(r'\[ERROR\]|BUILD FAILURE|COMPILATION ERROR|Tests run:.*(?:Failures: [1-9]|Errors: [1-9])')


def mode():
    value = os.getenv(MODE_ENV, 'auto').strip().lower()
    return value if value in ('on', 'off', 'auto') else 'auto'


def environment():
    env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
    for name in os.getenv(PASSTHROUGH_ENV, '').split(','):
        if name.strip() and name.strip() in os.environ:
            env[name.strip()] = os.environ[name.strip()]
    return env


def tools():
    """The Maven launcher and Java, or the name of what is missing."""
    mvn, java = shutil.which('mvn'), shutil.which('java')
    return mvn, java, [n for n, p in (('maven', mvn), ('java', java)) if not p]


def decide(flag):
    """Whether to run, given the caller's ``execute_build`` and the mode.

    ``flag`` is True/False from a caller who said so explicitly, or ``'auto'``
    (the default for the agents). Returns ``(run, reason_if_not)``.
    """
    if flag is False or str(flag).lower() == 'false':
        return False, 'execute_build=false'
    if mode() == 'off':
        return False, '%s=off' % MODE_ENV
    explicit = flag is True or str(flag).lower() == 'true' or mode() == 'on'
    _, _, missing = tools()
    if missing and not explicit:
        return False, 'not installed on this host: ' + ', '.join(missing)
    return True, ''


def build_pom(root):
    """The pom to build from: the root's, else the aggregating parent module's."""
    root = Path(root)
    if (root / 'pom.xml').exists():
        return root / 'pom.xml'
    for d in sorted(root.glob('*-parent')):
        if (d / 'pom.xml').exists():
            return d / 'pom.xml'
    for d in sorted(root.glob('*')):
        if (d / 'pom.xml').exists():
            return d / 'pom.xml'
    return None


def error_lines(output):
    """The lines that say why a build failed, in order, capped."""
    seen, out = set(), []
    for line in output.splitlines():
        if _ERROR.search(line) and line not in seen:
            seen.add(line)
            out.append(line.strip()[:300])
    return out[:MAX_ERROR_LINES]


def build(root, timeout=BUILD_TIMEOUT):
    """``mvn clean verify`` from the parent pom, then the skill's M24 clean-up."""
    pom = build_pom(root)
    mvn, _, missing = tools()
    if pom is None:
        return {'status': 'FAIL', 'reason': 'no pom.xml to build', 'errors': ['no pom.xml found']}
    if missing:
        return {'status': 'FAIL', 'reason': 'not installed on this host: ' + ', '.join(missing),
                'errors': ['%s is not installed, so the build could not run' % ', '.join(missing)]}
    cmd = [mvn, '-B', '-f', str(pom), 'clean', 'verify']
    started = now()
    try:
        done = subprocess.run(cmd, cwd=str(pom.parent), capture_output=True, text=True,
                              errors='replace', timeout=timeout, env=environment())
    except subprocess.TimeoutExpired:
        return {'status': 'TIMEOUT', 'command': cmd, 'started_at': started, 'completed_at': now(),
                'errors': ['build exceeded %d seconds' % timeout]}
    # The parent is a pure aggregator; its target/ is empty build metadata that
    # the skill says must not be handed off (gap-checklist M24).
    for d in Path(root).glob('*-parent'):
        shutil.rmtree(d / 'target', ignore_errors=True)
    output = done.stdout + '\n' + done.stderr
    return {'status': 'PASS' if done.returncode == 0 else 'FAIL', 'command': cmd,
            'returncode': done.returncode, 'started_at': started, 'completed_at': now(),
            'stdout_hash': digest(done.stdout), 'stderr_hash': digest(done.stderr),
            'errors': [] if done.returncode == 0 else (error_lines(output) or output.strip().splitlines()[-10:])}


def packaged_jar(root):
    jars = [j for j in Path(root).rglob('target/*.jar')
            if not j.name.endswith(('-sources.jar', '-javadoc.jar', '.original', '-tests.jar'))
            and '-parent' not in j.parent.parent.name]
    return max(jars, key=lambda j: j.stat().st_size) if jars else None


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start(root, timeout=START_TIMEOUT):
    """Run the packaged jar on a free port until it answers HTTP, then stop it.

    Any HTTP answer counts, including a 503 from ``/actuator/health``: the
    Spring context started and the web server is listening, which is what
    "starts" means here. A health that is DOWN because a backend is unreachable
    from this host is a different problem and is reported in the evidence.
    """
    jar = packaged_jar(root)
    _, java, missing = tools()
    if jar is None:
        return {'status': 'FAIL', 'errors': ['no packaged jar under target/; the build produced none']}
    if 'java' in missing:
        return {'status': 'FAIL', 'errors': ['java is not installed, so the service could not be started']}
    port = _free_port()
    log = Path(jar).with_suffix('.start.log')
    cmd = [java, '-jar', str(jar), '--spring.profiles.active=local', '--server.port=%d' % port]
    with open(log, 'w', encoding='utf-8', errors='replace') as sink:
        proc = subprocess.Popen(cmd, cwd=str(jar.parent), stdout=sink, stderr=subprocess.STDOUT,
                                env=environment())
        answered, deadline = None, time.time() + timeout
        try:
            while time.time() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/actuator/health' % port, timeout=3) as r:
                        answered = r.status
                except urllib.error.HTTPError as e:
                    answered = e.code
                except (OSError, ValueError):
                    time.sleep(2)
                    continue
                break
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
    tail = log.read_text(encoding='utf-8', errors='replace').splitlines()[-MAX_LOG_LINES:]
    try:
        log.unlink()
    except OSError:
        pass
    if answered is not None:
        return {'status': 'PASS', 'http_status': answered, 'jar': jar.name}
    exited = proc.returncode is not None and proc.returncode not in (0, None)
    return {'status': 'FAIL', 'jar': jar.name,
            'errors': [x[:300] for x in tail if re.search(r'(?i)error|exception|failed|caused by', x)][:MAX_ERROR_LINES]
                      or tail[-10:] or ['the service did not answer within %d seconds' % timeout],
            'reason': 'exited with code %s' % proc.returncode if exited else 'no answer within %d seconds' % timeout}


def checks(root, flag='auto'):
    """The gate's build checks: COMPILE_AND_UNIT_TEST and STARTUP_SMOKE."""
    run, why = decide(flag)
    if not run:
        return [{'name': 'COMPILE_AND_UNIT_TEST', 'status': 'NOT_EXECUTED', 'evidence': why},
                {'name': 'STARTUP_SMOKE', 'status': 'NOT_EXECUTED', 'evidence': why}]
    built = build(root)
    out = [{'name': 'COMPILE_AND_UNIT_TEST', 'status': 'PASS' if built['status'] == 'PASS' else 'FAIL',
            'evidence': built}]
    if built['status'] != 'PASS':
        out.append({'name': 'STARTUP_SMOKE', 'status': 'NOT_EXECUTED', 'evidence': 'the build did not pass'})
        return out
    started = start(root)
    out.append({'name': 'STARTUP_SMOKE', 'status': started['status'], 'evidence': started})
    return out
