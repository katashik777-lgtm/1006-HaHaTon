"""Landlock filesystem confinement and a seccomp allowlist for hosted Python."""
import ctypes as c
import errno
import os
from pathlib import Path
import resource
import runpy
import sys


class PathRule(c.Structure):
    _pack_ = 1
    _fields_ = [('rights', c.c_uint64), ('parent_fd', c.c_int32)]


class Arg(c.Structure):
    _fields_ = [('index', c.c_uint), ('operator', c.c_int), ('a', c.c_uint64), ('b', c.c_uint64)]


def restrict_files(read_only, scratch):
    libc = c.CDLL(None, use_errno=True)
    libc.syscall.restype = c.c_long
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(c.get_errno(), 'no_new_privs')
    # x86-64 and aarch64 share the Landlock syscall numbers.
    if os.uname().machine not in ('x86_64', 'aarch64'):
        raise RuntimeError('Unsupported Landlock architecture')
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 5:
        raise RuntimeError('Landlock ABI 5 is required')
    handled = (1 << 16) - 1
    rules = c.c_uint64(handled)
    fd = libc.syscall(444, c.byref(rules), c.sizeof(rules), 0)
    if fd < 0:
        raise OSError(c.get_errno(), 'landlock_create_ruleset')
    try:
        read_file, read_dir = 1 << 2, 1 << 3
        scratch_rights = read_file | read_dir | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 7) | (1 << 8) | (1 << 14)
        paths = [(p, read_file | (read_dir if p.is_dir() else 0)) for p in read_only if p.exists()]
        paths += [(scratch, scratch_rights)]
        for path, rights in paths:
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(rights, parent)
                if libc.syscall(445, fd, 1, c.byref(rule), 0) != 0:
                    raise OSError(c.get_errno(), 'landlock_add_rule')
            finally:
                os.close(parent)
        if libc.syscall(446, fd, 0) != 0:
            raise OSError(c.get_errno(), 'landlock_restrict_self')
    finally:
        os.close(fd)


def syscall_filter():
    sec = c.CDLL('libseccomp.so.2')
    sec.seccomp_init.argtypes = [c.c_uint32]
    sec.seccomp_init.restype = c.c_void_p
    sec.seccomp_release.argtypes = [c.c_void_p]
    sec.seccomp_load.argtypes = [c.c_void_p]
    sec.seccomp_syscall_resolve_name.argtypes = [c.c_char_p]
    sec.seccomp_rule_add_array.argtypes = [c.c_void_p, c.c_uint32, c.c_int, c.c_uint, c.POINTER(Arg)]
    allow = 0x7fff0000
    error_action = 0x00050000
    ctx = sec.seccomp_init(error_action | errno.EPERM)
    if not ctx:
        raise RuntimeError('Could not create seccomp filter')

    def rule(name, action=allow, arg=None):
        number = sec.seccomp_syscall_resolve_name(name.encode())
        if number < 0:
            return
        if sec.seccomp_rule_add_array(ctx, action, number, int(arg is not None), c.byref(arg) if arg is not None else None) != 0:
            raise RuntimeError('Could not set seccomp rule ' + name)

    try:
        for name in '''
            read write readv writev pread64 pwrite64 preadv pwritev lseek close close_range
            open openat openat2 stat lstat fstat newfstatat statx statfs fstatfs
            access faccessat faccessat2 getdents getdents64 readlink readlinkat getcwd chdir
            mkdir mkdirat rmdir unlink unlinkat rename renameat renameat2 ftruncate truncate fsync fdatasync
            mmap mmap2 mprotect munmap mremap brk madvise mincore
            futex futex_waitv set_robust_list set_tid_address rseq membarrier
            sched_getaffinity sched_yield sched_getparam sched_getscheduler
            getpid getppid gettid getuid geteuid getgid getegid getgroups getcpu uname sysinfo
            gettimeofday clock_gettime clock_getres nanosleep clock_nanosleep getrandom
            rt_sigaction rt_sigprocmask rt_sigreturn sigaltstack restart_syscall
            dup dup2 dup3 pipe pipe2 select pselect6 poll ppoll epoll_create1 epoll_ctl epoll_wait
            getrlimit getrusage times exit exit_group
        '''.split():
            rule(name)
        for cmd in (0, 1, 2, 3, 4, 5, 6, 7, 1030):  # descriptor flags and ordinary locks
            rule('fcntl', arg=Arg(1, 4, cmd, 0))
        rule('prlimit64', arg=Arg(0, 4, 0, 0))
        # glibc pthread uses clone3 first. Allow only the exact pthread clone flags.
        rule('clone3', error_action | errno.ENOSYS)
        rule('clone', arg=Arg(0, 4, 0x3D0F00, 0))
        if sec.seccomp_load(ctx) != 0:
            raise RuntimeError('Could not enforce seccomp')
    finally:
        sec.seccomp_release(ctx)


def main():
    script, scratch, artifact, mode, *args = sys.argv[1:]
    scratch = Path(scratch).resolve()
    runtime = Path(sys.base_prefix).resolve()
    libs = (runtime / 'lib', Path('/usr/lib'), Path('/lib'), Path('/lib64'),
            Path('/etc/ld.so.cache'), Path('/etc/localtime'), Path('/dev/urandom'), Path('/dev/null'),
            # Read-only process metadata used by DuckDB's memory-limit detection.
            Path('/proc/self/cgroup'), Path('/proc/self/mountinfo'),
            Path('/sys/fs/cgroup/memory.max'), Path('/sys/fs/cgroup/memory.current'),
            Path('/sys/fs/cgroup/cpu.max'), Path('/sys/devices/system/cpu/online'))
    allowed = [*libs, Path(__file__).resolve().parent]
    if artifact != '-':
        allowed.append(Path(artifact).resolve())
    # Load the filter library before Landlock, and keep its runtime libraries readable.
    c.CDLL('libseccomp.so.2')
    limits = (768, 15) if mode == 'check' else (256, 45)
    for kind, value in ((resource.RLIMIT_AS, limits[0] * 1024**2), (resource.RLIMIT_CPU, limits[1]),
                        (resource.RLIMIT_FSIZE, 4 * 1024**2), (resource.RLIMIT_NOFILE, 128),
                        (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (value, value))
    restrict_files(allowed, scratch)
    syscall_filter()
    if mode == 'probe':
        print('isolated')
        return
    sys.argv = [script, *args]
    os.environ['SPR_SANDBOX_APPLIED'] = '1'
    runpy.run_path(script, run_name='__main__')


if __name__ == '__main__':
    main()
