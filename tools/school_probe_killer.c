/*
 * school_probe_killer — A1 native blocker for Tomorrow School idle-logout.sh
 *
 * School does:
 *   idle=$(osascript … CGEventSourceSecondsSinceLastEventType(1, t) …)
 *   [[ "$idle" =~ ^[0-9]+$ ]] || exit 0
 *
 * Probe lifetime ≈ 45ms. Busy-scan osascript argv; SIGKILL before it returns.
 * Needle must NOT match stay_via_firefox.school_idle_seconds() (uses (1, x)).
 */

#include <libproc.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

static const char *NEEDLES[] = {
    "CGEventSourceSecondsSinceLastEventType(1, t)",
    /* UTF-8 Автовыход — school warn dialog title */
    "\xD0\x90\xD0\xB2\xD1\x82\xD0\xBE\xD0\xB2\xD1\x8B\xD1\x85\xD0\xBE\xD0\xB4",
    NULL,
};

static int g_verbose = 0;
static volatile sig_atomic_t g_stop = 0;
static unsigned long long g_kills = 0;
static unsigned long long g_scans = 0;

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

static int cmdline_contains_needle(pid_t pid) {
    int mib[3] = {CTL_KERN, KERN_PROCARGS2, (int)pid};
    size_t size = 0;

    if (sysctl(mib, 3, NULL, &size, NULL, 0) != 0 || size == 0 || size > 1024 * 1024)
        return 0;

    char *buf = malloc(size);
    if (!buf)
        return 0;

    if (sysctl(mib, 3, buf, &size, NULL, 0) != 0) {
        free(buf);
        return 0;
    }

    int hit = 0;
    for (const char **n = NEEDLES; *n; n++) {
        size_t nlen = strlen(*n);
        if (nlen == 0 || nlen > size)
            continue;
        for (size_t i = 0; i + nlen <= size; i++) {
            if (memcmp(buf + i, *n, nlen) == 0) {
                hit = 1;
                break;
            }
        }
        if (hit)
            break;
    }
    free(buf);
    return hit;
}

static unsigned kill_matching_osascripts(void) {
    int bufsize = proc_listpids(PROC_ALL_PIDS, 0, NULL, 0);
    if (bufsize <= 0)
        return 0;

    /* Ask again with a little headroom — PID table can grow between calls */
    bufsize += (int)sizeof(pid_t) * 32;
    pid_t *pids = calloc((size_t)bufsize / sizeof(pid_t), sizeof(pid_t));
    if (!pids)
        return 0;

    int nbytes = proc_listpids(PROC_ALL_PIDS, 0, pids, bufsize);
    if (nbytes <= 0) {
        free(pids);
        return 0;
    }

    int n = nbytes / (int)sizeof(pid_t);
    unsigned killed = 0;
    pid_t self = getpid();

    for (int i = 0; i < n; i++) {
        pid_t pid = pids[i];
        if (pid <= 1 || pid == self)
            continue;

        char name[64];
        if (proc_name(pid, name, sizeof name) <= 0)
            continue;
        if (strcmp(name, "osascript") != 0)
            continue;

        if (!cmdline_contains_needle(pid))
            continue;

        if (kill(pid, SIGKILL) == 0) {
            killed++;
            g_kills++;
            if (g_verbose)
                fprintf(stderr, "KILL pid=%d\n", (int)pid);
        }
    }

    free(pids);
    return killed;
}

static void usage(const char *argv0) {
    fprintf(stderr,
            "Usage: %s [--verbose] [--workers N] [--duration SEC]\n"
            "  Busy-loop killer for school idle-logout osascript probes.\n",
            argv0);
}

int main(int argc, char **argv) {
    int workers = 2;
    int duration = 0;
    int verbose = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--verbose") == 0 || strcmp(argv[i], "-v") == 0) {
            verbose = 1;
        } else if (strcmp(argv[i], "--workers") == 0 && i + 1 < argc) {
            workers = atoi(argv[++i]);
            if (workers < 1)
                workers = 1;
            if (workers > 8)
                workers = 8;
        } else if (strcmp(argv[i], "--duration") == 0 && i + 1 < argc) {
            duration = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            usage(argv[0]);
            return 0;
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    g_verbose = verbose;

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    for (int w = 1; w < workers; w++) {
        pid_t kid = fork();
        if (kid == 0) {
            workers = 1;
            break;
        }
    }

    time_t started = time(NULL);
    time_t last_hb = started;
    fprintf(stderr, "school_probe_killer pid=%d uid=%d\n", (int)getpid(), (int)getuid());

    while (!g_stop) {
        kill_matching_osascripts();
        g_scans++;

        if (duration > 0 && (int)(time(NULL) - started) >= duration)
            break;

        if (!g_verbose && (time(NULL) - last_hb) >= 60) {
            fprintf(stderr, "heartbeat scans=%llu kills=%llu\n", g_scans, g_kills);
            last_hb = time(NULL);
        }
    }

    fprintf(stderr, "exit scans=%llu kills=%llu\n", g_scans, g_kills);
    return 0;
}
