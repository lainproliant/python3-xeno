# --------------------------------------------------------------------
# shell.py
#
# Author: Lain Musgrove (lain.proliant@gmail.com)
# Date: Saturday October 24, 2020
#
# Distributed under terms of the MIT license.
# --------------------------------------------------------------------

import os
import asyncio
import shlex
import subprocess
from typing import Any, Callable, Dict, Optional, Tuple
from pathlib import Path

from xeno.utils import is_iterable, decode

# --------------------------------------------------------------------
EnvDict = Dict[str, Any]
InputSource = Callable[[], str]
LineSink = Callable[[str, asyncio.StreamWriter], None]
OutputTaskData = Tuple[asyncio.StreamReader, LineSink]

# --------------------------------------------------------------------
def digest_env(env: EnvDict):
    flat_env: Dict[str, str] = {}
    for key, value in env.items():
        if is_iterable(value):
            value = " ".join(shlex.quote(str(s)) for s in value)
        flat_env[key] = str(value)
    return flat_env


# --------------------------------------------------------------------
def digest_params(params: EnvDict):
    flat_params: Dict[str, str] = {}
    for key, value in params.items():
        if is_iterable(value):
            value = " ".join(str(s) for s in value)
        flat_params[key] = str(value)
    return flat_params


# --------------------------------------------------------------------
def check(cmd):
    return subprocess.check_output(shlex.split(cmd)).decode("utf-8").strip()


# --------------------------------------------------------------------
class Shell:
    def __init__(self, env: EnvDict = dict(os.environ), cwd: Optional[Path] = None):
        self._env = digest_env(env)
        self._cwd = cwd or Path.cwd()

    def env(self, new_env: EnvDict):
        return Shell({**self._env, **new_env}, self._cwd)

    def cd(self, new_cwd: Path):
        assert new_cwd.exists() and new_cwd.is_dir(), "Invalid directory provided."
        return Shell(self._env, new_cwd)

    # pylint: disable=no-member
    # see: https://github.com/PyCQA/pylint/issues/1469
    async def _create_proc(self, cmd: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
            shell=True,
        )

    def _interact(self, cmd: str, check: bool) -> int:
        returncode = subprocess.call(
            cmd,
            env=self._env,
            cwd=self._cwd,
            shell=True
        )
        assert not check or returncode == 0, "Command failed."
        return returncode

    def _interpolate_cmd(self, cmd: str, params: EnvDict) -> str:
        return cmd.format(**self._env, **(digest_params(params)))

    async def run(
        self,
        cmd: str,
        stdin: Optional[InputSource] = None,
        stdout: Optional[LineSink] = None,
        stderr: Optional[LineSink] = None,
        check=False,
        **params
    ) -> int:

        rl_tasks: Dict[asyncio.Future[Any], OutputTaskData] = {}

        def setup_rl_task(stream: asyncio.StreamReader, sink: LineSink):
            rl_tasks[asyncio.Task(stream.readline())] = (stream, sink)

        cmd = self._interpolate_cmd(cmd, params)
        proc = await self._create_proc(cmd)
        assert proc.stdout is not None
        assert proc.stderr is not None
        if stdin:
            assert proc.stdin
            proc.stdin.write(stdin().encode("utf-8"))
        if stdout:
            setup_rl_task(proc.stdout, stdout)
        if stderr:
            setup_rl_task(proc.stderr, stderr)

        while rl_tasks:
            done, pending = await asyncio.wait(
                rl_tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for future in done:
                stream, sink = rl_tasks.pop(future)
                line = future.result()
                if line:
                    line = decode(line).strip()
                    assert proc.stdin is not None
                    sink(line, proc.stdin)
                    setup_rl_task(stream, sink)

        await proc.wait()
        assert not check or proc.returncode == 0, "Command failed."
        return proc.returncode

    def sync(
        self,
        cmd: str,
        stdin: Optional[InputSource] = None,
        stdout: Optional[LineSink] = None,
        stderr: Optional[LineSink] = None,
        check=False,
        **params
    ) -> int:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.run(cmd, stdin, stdout, stderr, check, **params))

    def interact(self, cmd: str, check=False, **params) -> int:
        cmd = self._interpolate_cmd(cmd, params)
        return self._interact(cmd, check)
