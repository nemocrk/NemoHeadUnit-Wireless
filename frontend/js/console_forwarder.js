/**
 * Frontend Console Log Forwarder
 * Intercepts console.warn and console.error calls and forwards them asynchronously 
 * to the backend REST log API (/api/system/client_log), maintaining native devtools console output.
 */

(function initConsoleForwarder() {
    if (window._consoleForwarderInitialized) return;
    window._consoleForwarderInitialized = true;

    const originalWarn = console.warn;
    const originalError = console.error;

    function formatArgs(args) {
        return args.map(arg => {
            if (arg instanceof Error) {
                return `${arg.name}: ${arg.message}\n${arg.stack || ''}`;
            }
            if (typeof arg === 'object') {
                try {
                    return JSON.stringify(arg);
                } catch (e) {
                    return String(arg);
                }
            }
            return String(arg);
        }).join(' ');
    }

    function forwardToBackend(level, message) {
        try {
            fetch('/api/system/client_log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    level: level,
                    message: message,
                    module: 'webclient'
                })
            }).catch(() => {});
        } catch (e) {}
    }

    console.warn = function(...args) {
        originalWarn.apply(console, args);
        forwardToBackend('WARN', formatArgs(args));
    };

    console.error = function(...args) {
        originalError.apply(console, args);
        forwardToBackend('ERROR', formatArgs(args));
    };
})();
