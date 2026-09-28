"""Platform-specific hard memory scopes. Explicit requests never silently degrade."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import sys
import time
import uuid

from wavebench.errors import ConfigError


class _NoMemoryScope:
    def attach(self, pid):
        pass

    def evidence(self):
        return {'memory_backend': 'none', 'memory_accounting': 'estimated_working_set'}

    def close(self):
        pass


class _LinuxMemoryScope:
    def __init__(self, policy):
        if not policy.cgroup_root:
            raise ConfigError('Linux hard memory limit requires a delegated cgroup_root')
        root = Path(policy.cgroup_root).resolve()
        if not (root / 'cgroup.controllers').is_file():
            raise ConfigError('cgroup_root must be a delegated cgroup v2 directory')
        self.path = root / f'wavebench-analysis-{uuid.uuid4().hex}'
        self.path.mkdir()
        try:
            for name in ('memory.max', 'memory.swap.max', 'cgroup.procs', 'cgroup.kill', 'memory.events'):
                if not (self.path / name).is_file():
                    raise ConfigError(f'cgroup memory backend requires {name}')
            (self.path / 'memory.max').write_text(str(policy.memory_bytes), encoding='ascii')
            (self.path / 'memory.swap.max').write_text('0', encoding='ascii')
            # Opening checks permission without moving any process or killing the group.
            for name in ('cgroup.procs', 'cgroup.kill'):
                descriptor = os.open(self.path / name, os.O_WRONLY)
                os.close(descriptor)
            self.limit = policy.memory_bytes
        except BaseException:
            self.path.rmdir()
            raise

    def attach(self, pid):
        (self.path / 'cgroup.procs').write_text(str(pid), encoding='ascii')

    def evidence(self):
        events = dict(line.split() for line in (self.path / 'memory.events').read_text().splitlines())
        return {'memory_backend': 'linux_cgroup_v2', 'memory_accounting': 'cgroup_memory_swap_disabled',
                'memory_limit_bytes': self.limit, 'oom_kill_count': int(events.get('oom_kill', 0))}

    def close(self):
        (self.path / 'cgroup.kill').write_text('1', encoding='ascii')
        # Kernel removal may lag task exit briefly. Never remove or alter the delegated parent.
        for attempt in range(100):
            try:
                self.path.rmdir()
                return
            except OSError:
                if attempt == 99:
                    raise
                time.sleep(0.01)


class _WindowsMemoryScope:
    def __init__(self, policy):
        if policy.cgroup_root is not None:
            raise ConfigError('cgroup_root is only supported on Linux')
        from ctypes import wintypes as w
        size = ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('working_min', size), ('working_max', size),
                        ('active_processes', w.DWORD), ('affinity', size),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', size), ('job_memory', size),
                        ('peak_process_memory', size), ('peak_job_memory', size)]

        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'CreateJobObjectW': ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x200 | 0x2000  # JOB_MEMORY | KILL_ON_JOB_CLOSE
        limits.job_memory = policy.memory_bytes
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        self.limit = policy.memory_bytes

    def attach(self, pid):
        handle = self.api.OpenProcess(0x100 | 0x1, False, pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)

    def evidence(self):
        return {'memory_backend': 'windows_job_object', 'memory_accounting': 'job_committed_memory',
                'memory_limit_bytes': self.limit}

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def memory_scope(policy):
    scope = None
    try:
        if policy.memory_bytes is None:
            scope = _NoMemoryScope()
        elif sys.platform == 'win32':
            scope = _WindowsMemoryScope(policy)
        elif sys.platform == 'linux':
            scope = _LinuxMemoryScope(policy)
        else:
            raise ConfigError('hard analysis memory limits are unsupported on this platform')
        yield scope
    except OSError as exc:
        raise ConfigError(f'cannot establish or clean up analysis memory scope: {exc}') from exc
    finally:
        if scope is not None:
            try:
                scope.close()
            except OSError as exc:
                raise ConfigError(f'cannot clean up analysis memory scope: {exc}') from exc
