class LanguageDetector {
    constructor() {
        this.languages = new Map([
            // Existing Languages
            ['javascript', {
                patterns: [
                    /\b(const|let|var)\s+\w+\s*=/,
                    /\bfunction\s+\w*\s*\([^)]*\)\s*\{/,
                    /=>\s*\{|\([^)]*\)\s*=>/,
                    /\bconsole\.(log|error|warn|info|assert|dir)\(/,
                    /\bdocument\.(querySelector|getElementById|getElementsByClassName|createElement)\(/,
                    /\b(import|export)\s+[^;]+;/,
                    /\bnew\s+Promise\s*\(/,
                    /\bclass\s+\w+\s*(extends\s+\w+)?\s*\{/,
                    /\basync\s+function\b/,
                    /`[^`]*\${[^}]+}[^`]*`/,
                    /\/[^\/\n]+\/[gimsuy]*/,
                    /\brequire\(['"][^'"]+['"]\)/,
                    /\bmodule\.exports\s*=/,
                    /\bprototype\b/,
                    /\bthis\b/,
                    /\bprocess\.\w+/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                ],
                keywords: new Set([
                    'async', 'await', 'class', 'const', 'let', 'var', 'function',
                    'return', 'if', 'else', 'for', 'while', 'do', 'switch', 'case',
                    'break', 'continue', 'throw', 'try', 'catch', 'finally',
                    'typeof', 'instanceof', 'new', 'delete', 'this', 'super',
                    'yield', 'import', 'export', 'default', 'extends', 'console',
                    'window', 'global', 'process', 'require', 'module', 'exports',
                    'NaN', 'Infinity', 'undefined', 'null', 'true', 'false'
                ]),
                minMatches: 4
            }],
            ['python', {
                patterns: [
                    /^\s*(def|class)\s+\w+[\s\(:]/m,
                    /^\s*@\w+/m,
                    /\b(import|from)\s+[\w.]+/,
                    /^\s*(if|elif|else|while|for|try|except|finally|with)\b.*:/m,
                    /\b(print|input|len|range|list|dict|set|tuple|open)\b/,
                    /'''[\s\S]*?'''|"""[\s\S]*?"""/,
                    /\b(async\s+def|await\s+|yield\s+from)\b/,
                    /\[[^\]]+\s+for\s+\w+\s+in\s+[^\]]+\]/,  // List comprehensions
                    /^#!.*python[23]?/,
                    /if\s+__name__\s*==\s*['"]__main__['"]:/,
                    /\blambda\s+[\w,]*:\s+/,
                    /\bdef\s+\w+\(.*\)\s*->\s*\w+:/,
                    /#.*$/,                        // Single-line comments
                ],
                keywords: new Set([
                    'def', 'class', 'if', 'elif', 'else', 'for', 'while', 'try',
                    'except', 'finally', 'with', 'as', 'import', 'from', 'return',
                    'and', 'or', 'not', 'is', 'in', 'lambda', 'pass', 'break',
                    'continue', 'global', 'nonlocal', 'assert', 'yield', 'del',
                    'raise', 'async', 'await', 'True', 'False', 'None', 'self',
                    'print', 'input', 'len', 'range', 'list', 'dict', 'set', 'tuple',
                    'int', 'float', 'str', '__name__', '__main__'
                ]),
                minMatches: 3
            }],
            ['java', {
                patterns: [
                    /\b(public|private|protected)\s+class\s+\w+/,
                    /\b(public|private|protected)\s+(static\s+)?\w+\s+\w+\s*\([^)]*\)\s*\{/,
                    /\bSystem\.(out|err)\.(println|print)\(/,
                    /\b(import|package)\s+[\w.]+;/,
                    /\b(extends|implements)\s+\w+/,
                    /@Override\b/,
                    /\bpublic\s+static\s+void\s+main\s*\(\s*String\s*\[\]\s*\w*\)\s*\{/,
                    /\btry\s*\{[\s\S]*?\}\s*catch\s*\(\s*\w+\s+\w+\)\s*\{/,
                    /\bnew\s+\w+\s*\(/,
                    /\bthrows\s+\w+/,
                    /\bsynchronized\s*\(.*\)\s*\{/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                ],
                keywords: new Set([
                    'public', 'private', 'protected', 'class', 'interface', 'extends',
                    'implements', 'static', 'final', 'void', 'return', 'abstract',
                    'assert', 'boolean', 'break', 'byte', 'case', 'catch', 'char',
                    'continue', 'default', 'double', 'enum', 'finally', 'float',
                    'import', 'instanceof', 'int', 'long', 'native', 'new', 'null',
                    'package', 'short', 'strictfp', 'super', 'switch', 'synchronized',
                    'this', 'throw', 'throws', 'transient', 'try', 'volatile', 'while',
                    'System', 'out', 'println', 'String', 'Exception'
                ]),
                minMatches: 4
            }],
            ['typescript', {
                patterns: [
                    /\b(interface|type|enum)\s+\w+(\s*<[^>]+>)?\s*(\{|=)/,
                    /:\s*(string|number|boolean|any|void|never|unknown|undefined)\b/,
                    /\b(public|private|protected|readonly)\s+\w+\s*:\s*\w+/,
                    /\bclass\s+\w+(\s*<[^>]+>)?\s+implements\s+\w+/,
                    /import\s+[\s\S]+?\s+from\s+['"][^'"]+['"]/,
                    /\w+\s*<\s*\w+(\s*,\s*\w+)*\s*>/,
                    /\w+\s+as\s+\w+/,
                    /@\w+\s*(\(\s*[^)]*\))?/,
                    /\bnamespace\s+\w+\s*\{/,
                    /\bdeclare\s+(module|function|const|let|var|class|interface)\b/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                ],
                keywords: new Set([
                    'interface', 'type', 'enum', 'implements', 'declare', 'namespace',
                    'readonly', 'private', 'public', 'protected', 'abstract',
                    'any', 'unknown', 'never', 'readonly', 'keyof', 'typeof',
                    'extends', 'module', 'import', 'export', 'as', 'from', 'string',
                    'number', 'boolean', 'void', 'undefined', 'null', 'let', 'const',
                    'class', 'function'
                ]),
                minMatches: 4
            }],
            ['html', {
                patterns: [
                    /<!DOCTYPE\s+html>/i,
                    /<html\b[^>]*>/i,
                    /<\/html>/i,
                    /<head\b[^>]*>/i,
                    /<\/head>/i,
                    /<body\b[^>]*>/i,
                    /<\/body>/i,
                    /<(div|span|p|a|img|script|link|meta|title|h[1-6]|ul|ol|li|table|tr|td|th|form|input|textarea|button|select|option|style|header|footer|nav|section|article|aside)[^>]*>/i,
                    /<\/(div|span|p|a|img|script|link|meta|title|h[1-6]|ul|ol|li|table|tr|td|th|form|input|textarea|button|select|option|style|header|footer|nav|section|article|aside)>/i,
                    /<!--[\s\S]*?-->/,
                    /<!\[CDATA\[[\s\S]*?\]\]>/,
                    /<\w+[^>]*\s(on\w+|style|class|id)\s*=\s*['"][^'"]*['"][^>]*>/i,
                    /<\w+[^>]*\s*\/>/i,
                    /&[a-z]+;/i,
                    /<script\b[^>]*>/i,
                    /<\/script>/i,
                ],
                keywords: new Set([
                    'doctype', 'html', 'head', 'body', 'div', 'span', 'class', 'id',
                    'style', 'href', 'src', 'alt', 'title', 'meta', 'link', 'script',
                    'form', 'input', 'textarea', 'button', 'select', 'option', 'table',
                    'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'ul', 'ol', 'li',
                    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'charset', 'content', 'http-equiv',
                    'rel', 'type', 'name', 'value', 'method', 'action', 'placeholder',
                    'header', 'footer', 'nav', 'section', 'article', 'aside', 'main',
                    'figure', 'figcaption', 'data', 'time', 'mark', 'template', 'canvas',
                    'svg', 'video', 'audio', 'source', 'track', 'embed', 'object', 'param'
                ]),
                minMatches: 3
            }],
            ['css', {
                patterns: [
                    /[.#]?[\w-]+\s*\{[^}]*\}/,
                    /@media\s*\([^)]+\)\s*\{/,
                    /@(keyframes|import|charset|font-face|supports|page|viewport)\b/,
                    /\b(margin|padding|border|background|color|font|position|display|flex|grid|justify-content|align-items|width|height|animation|transition|transform):\s*[^;]+;/,
                    /:(hover|active|focus|before|after|first-child|last-child|nth-child|nth-of-type|visited|link)\b/,
                    /#[\w-]+\s*\{[^}]*\}/,
                    /\.[\w-]+\s*\{[^}]*\}/,
                    /\[.+\]\s*\{[^}]*\}/,
                    /--[\w-]+:\s*[^;]+;/,
                    /\/\*[\s\S]*?\*\//,             // CSS comments
                ],
                keywords: new Set([
                    'margin', 'padding', 'border', 'color', 'background',
                    'position', 'display', 'flex', 'grid', 'justify-content', 'align-items',
                    'transform', 'transition', 'animation', 'width', 'height',
                    'max-width', 'min-width', 'max-height', 'min-height', 'overflow',
                    'background-color', 'border-radius', 'box-shadow', 'font-size',
                    'line-height', 'z-index', 'float', 'clear', 'content', 'important',
                    'opacity', 'visibility', 'cursor', 'pointer-events', 'text-align',
                    'vertical-align', 'white-space', 'word-wrap', 'text-decoration',
                    'list-style', 'outline', 'filter', 'clip', 'flex-direction',
                    'flex-wrap', 'align-content', 'order', 'flex-grow', 'flex-shrink',
                    'flex-basis', 'background-image', 'background-repeat',
                    'background-position', 'background-size'
                ]),
                minMatches: 3
            }],

            // New Languages
            ['c', {
                patterns: [
                    /\b#include\s+<[^>]+>/,
                    /\b(int|float|double|char|long|short|void)\s+\w+\s*;/,
                    /\bprintf\s*\(/,
                    /\bscanf\s*\(/,
                    /\bmain\s*\([^)]*\)\s*\{/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                    /\bstruct\s+\w+\s*\{/,
                    /\btypedef\s+struct\b/,
                ],
                keywords: new Set([
                    'include', 'printf', 'scanf', 'main', 'int', 'float', 'double', 'char',
                    'long', 'short', 'void', 'struct', 'typedef', 'return', 'if', 'else',
                    'for', 'while', 'do', 'switch', 'case', 'break', 'continue', 'goto',
                    'sizeof', 'static', 'const', 'volatile', 'extern', 'register', 'union'
                ]),
                minMatches: 3
            }],
            ['cpp', {
                patterns: [
                    /\b#include\s+<[^>]+>/,
                    /\b(std::)?(vector|map|string|cout|cin|endl)\b/,
                    /\btemplate\s*<[^>]+>/,
                    /\bclass\s+\w+\s*(?:[:]|;)/,
                    /\busing\s+namespace\s+\w+/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                    /\bnew\s+\w+\s*\(/,
                    /\bdelete\s+\w+/,
                ],
                keywords: new Set([
                    'include', 'vector', 'map', 'string', 'cout', 'cin', 'endl', 'template',
                    'class', 'public', 'private', 'protected', 'using', 'namespace', 'new',
                    'delete', 'this', 'nullptr', 'std', 'auto', 'const', 'constexpr', 'static',
                    'virtual', 'override', 'friend', 'operator', 'typename', 'typedef',
                    'decltype', 'inline', 'mutable', 'explicit', 'register', 'volatile',
                    'struct', 'union', 'enum', 'throw', 'try', 'catch', 'final', 'noexcept'
                ]),
                minMatches: 4
            }],
            ['ruby', {
                patterns: [
                    /^\s*(def|class)\s+\w+/m,
                    /\bend\b/,
                    /\bputs\s+["']/,
                    /\brequire\s+["']/,
                    /\bmodule\s+\w+/,
                    /\binclude\s+\w+/,
                    /#.*$/,                        // Single-line comments
                    /=begin[\s\S]*?=end/,          // Multi-line comments
                    /\b@{0,2}\w+/,                  // Instance and class variables
                    /\bdo\s+|\bdo\s*\|.*\|\s*\{/,
                ],
                keywords: new Set([
                    'def', 'class', 'end', 'module', 'include', 'require', 'puts', 'unless',
                    'elsif', 'begin', 'rescue', 'ensure', 'yield', 'self', 'super', 'attr_accessor',
                    'attr_reader', 'attr_writer', 'initialize', 'nil', 'true', 'false', 'and',
                    'or', 'not', 'unless', 'case', 'when', 'then', 'redo', 'retry', 'next',
                    'break', 'return', 'alias', 'undef', 'defined?', 'super', 'self'
                ]),
                minMatches: 3
            }],
            ['go', {
                patterns: [
                    /\bpackage\s+\w+/,
                    /\bimport\s+\(/,
                    /\bfunc\s+\w+\s*\(/,
                    /\bdefer\s+\w+\s*\(/,
                    /\bgo\s+\w+\s*\(/,
                    /\bchan\s+(\w+|\<\-)/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                    /\bstruct\s+\{/,
                    /\binterface\s+\{/,
                ],
                keywords: new Set([
                    'package', 'import', 'func', 'defer', 'go', 'chan', 'struct',
                    'interface', 'var', 'const', 'map', 'range', 'select', 'type',
                    'return', 'if', 'else', 'for', 'switch', 'case', 'default',
                    'break', 'continue', 'goto', 'fallthrough', 'select', 'interface',
                    'range', 'select', 'type', 'nil', 'make', 'new', 'append', 'len',
                    'cap', 'close', 'copy', 'defer'
                ]),
                minMatches: 3
            }],
            ['php', {
                patterns: [
                    /<\?php/,
                    /\becho\s+["']/,
                    /\bfunction\s+\w+\s*\(/,
                    /\$\w+\s*=/,
                    /\bclass\s+\w+\s*\{/,
                    /\bnamespace\s+\w+/,
                    /\buse\s+\w+/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                    /\bpublic\s+(static\s+)?function\b/,
                ],
                keywords: new Set([
                    'echo', 'function', 'class', 'namespace', 'use', 'public', 'private',
                    'protected', 'static', 'var', 'const', 'extends', 'implements',
                    'abstract', 'interface', 'final', 'require', 'include', 'return',
                    'if', 'else', 'elseif', 'for', 'foreach', 'while', 'do', 'switch',
                    'case', 'break', 'continue', 'throw', 'try', 'catch', 'finally',
                    'array', 'isset', 'empty', 'die', 'exit', 'global', 'self', '$this',
                    '__construct', '__destruct'
                ]),
                minMatches: 3
            }],
            ['swift', {
                patterns: [
                    /\bimport\s+\w+/,
                    /\bfunc\s+\w+\s*\(/,
                    /\bclass\s+\w+\s*\{/,
                    /\bstruct\s+\w+\s*\{/,
                    /\benum\s+\w+\s*\{/,
                    /\bprotocol\s+\w+\s*\{/,
                    /\blet\s+\w+\s*=/,
                    /\bvar\s+\w+\s*=/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                    /\bextension\s+\w+\s*\{/,
                ],
                keywords: new Set([
                    'import', 'func', 'class', 'struct', 'enum', 'protocol', 'let', 'var',
                    'if', 'else', 'for', 'while', 'switch', 'case', 'break', 'continue',
                    'return', 'guard', 'defer', 'try', 'catch', 'throws', 'rethrows',
                    'throws', 'self', 'super', 'init', 'nil', 'true', 'false', 'where',
                    'associatedtype', 'typealias', 'extension', 'public', 'private',
                    'internal', 'fileprivate', 'open', 'static', 'weak', 'unowned'
                ]),
                minMatches: 3
            }],
            ['kotlin', {
                patterns: [
                    /\bimport\s+\w+/,
                    /\bfun\s+\w+\s*\(/,
                    /\bclass\s+\w+\s*(?:\(|:)/,
                    /\bobject\s+\w+\s*\{/,
                    /\bval\s+\w+\s*=/,
                    /\bvar\s+\w+\s*=/,
                    /\binterface\s+\w+\s*\{/,
                    /\bdata\s+class\s+\w+\s*\{/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                ],
                keywords: new Set([
                    'import', 'fun', 'class', 'object', 'val', 'var', 'interface',
                    'data', 'sealed', 'inner', 'companion', 'open', 'override',
                    'return', 'if', 'else', 'for', 'while', 'when', 'try', 'catch',
                    'finally', 'is', 'as', 'in', 'out', 'reified', 'typealias',
                    'this', 'super', 'constructor', 'init', 'by', 'lazy', 'inline',
                    'noinline', 'crossinline', 'vararg', 'suspend'
                ]),
                minMatches: 3
            }],
            ['rust', {
                patterns: [
                    /\bfn\s+\w+\s*\(/,
                    /\blet\s+\w+\s*=/,
                    /\bstruct\s+\w+\s*\{/,
                    /\benum\s+\w+\s*\{/,
                    /\bimpl\s+\w+\s*\{/,
                    /\buse\s+\w+/,
                    /\bmod\s+\w+/,
                    /\btrait\s+\w+\s*\{/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                ],
                keywords: new Set([
                    'fn', 'let', 'mut', 'struct', 'enum', 'impl', 'use', 'mod', 'trait',
                    'pub', 'crate', 'self', 'super', 'where', 'const', 'static', 'async',
                    'await', 'dyn', 'move', 'ref', 'Box', 'Result', 'Option', 'Some',
                    'None', 'match', 'if', 'else', 'for', 'while', 'loop', 'break',
                    'continue', 'return', 'impl', 'as', 'extern', 'unsafe', 'crate'
                ]),
                minMatches: 3
            }],
            ['csharp', {
                patterns: [
                    /\busing\s+System;/,
                    /\bnamespace\s+\w+/,
                    /\bclass\s+\w+\s*\{/,
                    /\bpublic\s+(class|struct|interface)\s+\w+/,
                    /\bConsole\.(WriteLine|ReadLine)\(/,
                    /\bstatic\s+void\s+Main\s*\(/,
                    /\bnew\s+\w+\s*\(/,
                    /\/\/.*$/,                      // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                ],
                keywords: new Set([
                    'using', 'namespace', 'class', 'struct', 'interface', 'public',
                    'private', 'protected', 'internal', 'static', 'void', 'int',
                    'string', 'bool', 'new', 'this', 'base', 'return', 'if', 'else',
                    'for', 'while', 'do', 'switch', 'case', 'break', 'continue',
                    'throw', 'try', 'catch', 'finally', 'finally', 'var', 'const',
                    'readonly', 'delegate', 'event', 'async', 'await', 'partial',
                    'sealed', 'override', 'virtual', 'abstract', 'default', 'params'
                ]),
                minMatches: 3
            }],
            ['perl', {
                patterns: [
                    /\buse\s+strict;/,
                    /\buse\s+warnings;/,
                    /\bprint\s+["']/,
                    /\bmy\s+\$\w+\s*=/,
                    /\bsub\s+\w+\s*\{/,
                    /\$[a-zA-Z_]\w*/,
                    /#!/,                           // Shebang
                    /#.*$/,                         // Single-line comments
                    /=begin[\s\S]*?=end/,           // Multi-line comments
                ],
                keywords: new Set([
                    'use', 'strict', 'warnings', 'print', 'my', 'sub', 'foreach', 'while',
                    'for', 'if', 'elsif', 'else', 'unless', 'unless', 'return', 'die',
                    'eval', 'package', 'use', 'our', 'local', 'goto', 'shift', 'unshift',
                    'push', 'pop', 'split', 'join', 'map', 'grep', 'scalar', 'ref',
                    'bless', 'delete', 'exists', 'keys', 'values', 'each', 'reverse',
                    'sort', 'lc', 'uc', 'chomp', 'chop', 'length', 'substr'
                ]),
                minMatches: 3
            }],
            ['bash', {
                patterns: [
                    /^#!/,                           // Shebang
                    /\becho\s+["']/,
                    /\bfunction\s+\w+\s*\{/,
                    /\bif\s+\[.*\];\s*then/,
                    /\bfor\s+\w+\s+in\s+.+;\s*do/,
                    /\bwhile\s+\[.*\];\s*do/,
                    /\bcase\s+.+\s+in/,
                    /#.*$/,                         // Single-line comments
                    /<<[-]?['"]?(\w+)['"]?/,        // Here documents
                    /^\s*(?:pip|pip3|python|python3|npm|npx|yarn|pnpm|node|docker|curl|wget|git|brew|apt|apt-get|sudo|cd|ls|mkdir|rm|cp|mv|cat|chmod|chown|ssh|scp|tar|grep|find|make|cargo|go|ruby|gem|composer|php|dotnet|java|javac|gcc|g\+\+|cmake|rustc)\b/m,  // Common CLI commands
                ],
                keywords: new Set([
                    'echo', 'function', 'if', 'then', 'else', 'elif', 'fi', 'for', 'in',
                    'do', 'done', 'while', 'case', 'esac', 'select', 'until', 'break',
                    'continue', 'return', 'declare', 'typeset', 'readonly', 'export',
                    'source', 'install', 'run', 'build', 'start', 'test', 'init',
                    '&&', '||', '!', '<<', '>>', '>|', '<&', '>&'
                ]),
                minMatches: 1
            }],
            ['sql', {
                patterns: [
                    /\bSELECT\b[\s\S]+?\bFROM\b/,
                    /\bINSERT\s+INTO\b/,
                    /\bUPDATE\s+\w+\s+\bSET\b/,
                    /\bDELETE\s+FROM\b/,
                    /\bCREATE\s+(DATABASE|TABLE)\b/,
                    /\bALTER\s+TABLE\b/,
                    /\bDROP\s+(DATABASE|TABLE)\b/,
                    /--.*$/,                        // Single-line comments
                    /\/\*[\s\S]*?\*\//,             // Multi-line comments
                    /\bWHERE\b/,
                    /\bJOIN\b/,
                ],
                keywords: new Set([
                    'SELECT', 'FROM', 'WHERE', 'INSERT', 'INTO', 'UPDATE', 'SET', 'DELETE',
                    'CREATE', 'TABLE', 'DATABASE', 'ALTER', 'DROP', 'JOIN', 'INNER', 'LEFT',
                    'RIGHT', 'FULL', 'ON', 'AS', 'DISTINCT', 'GROUP', 'BY', 'ORDER',
                    'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL', 'ANY', 'EXISTS',
                    'IN', 'LIKE', 'BETWEEN', 'IS', 'NULL', 'NOT', 'AND', 'OR'
                ]),
                minMatches: 3
            }],
            ['json', {
                patterns: [
                    /^\s*\{[\s\S]*\}\s*$/m,
                    /^\s*\[[\s\S]*\]\s*$/m,
                    /"[^"]+"\s*:/,
                    /,"[^"]+":/,
                    /:\s*("[^"]*"|\d+|true|false|null)/,
                ],
                keywords: new Set([
                    'true', 'false', 'null'
                ]),
                minMatches: 2
            }],
            ['yaml', {
                patterns: [
                    /^\s*\w+:\s+/m,
                    /^\s*-\s+/m,
                    /^\s*\w+\s*:\s*\w+/m,
                    /^\s*#.*$/m,
                    /^\s*\.\.\.$/m,
                    /^\s*---$/m,
                ],
                keywords: new Set([
                    'true', 'false', 'null', 'yes', 'no', 'on', 'off'
                ]),
                minMatches: 2
            }],
            ['markdown', {
                patterns: [
                    /^#\s+.+$/m,
                    /^##\s+.+$/m,
                    /^###\s+.+$/m,
                    /^-+\s*$/m,
                    /^\*\*\*$/m,
                    /\[.+\]\(.+\)/,
                    /!\[.+\]\(.+\)/,
                    /`{1,3}[\s\S]+?`{1,3}/,
                    /\n\s*\n/,
                ],
                keywords: new Set([
                    '#', '##', '###', '-', '*', '_', '`', '[]()', '![]()', '---', '***', '__', '**'
                ]),
                minMatches: 2
            }],
            ['shell', {
                patterns: [
                    /^#!/,                           // Shebang
                    /\becho\s+["']/,
                    /\bfunction\s+\w+\s*\{/,
                    /\bif\s+\[.*\];\s*then/,
                    /\bfor\s+\w+\s+in\s+.+;\s*do/,
                    /\bwhile\s+\[.*\];\s*do/,
                    /\bcase\s+.+\s+in/,
                    /#.*$/,                         // Single-line comments
                    /<<[-]?['"]?(\w+)['"]?/,        // Here documents
                ],
                keywords: new Set([
                    'echo', 'function', 'if', 'then', 'else', 'elif', 'fi', 'for', 'in',
                    'do', 'done', 'while', 'case', 'esac', 'select', 'until', 'break',
                    'continue', 'return', 'declare', 'typeset', 'readonly', 'export',
                    'source', '.', '&&', '||', '!', '<<', '>>', '>|', '<&', '>&'
                ]),
                minMatches: 2
            }],
            ['perl', {
                patterns: [
                    /\buse\s+strict;/,
                    /\buse\s+warnings;/,
                    /\bprint\s+["']/,
                    /\bmy\s+\$\w+\s*=/,
                    /\bsub\s+\w+\s*\{/,
                    /\$[a-zA-Z_]\w*/,
                    /#!/,                           // Shebang
                    /#.*$/,                         // Single-line comments
                    /=begin[\s\S]*?=end/,           // Multi-line comments
                ],
                keywords: new Set([
                    'use', 'strict', 'warnings', 'print', 'my', 'sub', 'foreach', 'while',
                    'for', 'if', 'elsif', 'else', 'unless', 'return', 'die', 'eval',
                    'package', 'use', 'our', 'local', 'goto', 'shift', 'unshift',
                    'push', 'pop', 'split', 'join', 'map', 'grep', 'scalar', 'ref',
                    'bless', 'delete', 'exists', 'keys', 'values', 'each', 'reverse',
                    'sort', 'lc', 'uc', 'chomp', 'chop', 'length', 'substr'
                ]),
                minMatches: 3
            }],

            // You can add more languages here following the same structure
        ]);
    }

    detectLanguage(code, customPatterns = new Map()) {
        // Combine default and custom patterns
        const allPatterns = new Map([...this.languages, ...customPatterns]);

        // Store matches for each language
        const matches = new Map();
        const codeLower = code.toLowerCase();

        for (const [lang, config] of allPatterns) {
            let matchCount = 0;
            const keywordsMatched = new Set();

            // Check patterns
            for (const pattern of config.patterns) {
                const regex = new RegExp(pattern.source, pattern.flags + 'g');
                if (regex.test(code)) {
                    matchCount += 1;
                }
            }

            // Check keywords
            const words = codeLower.match(/\b\w+\b/g) || [];
            for (const word of words) {
                if (config.keywords.has(word) && !keywordsMatched.has(word)) {
                    matchCount += 0.5; // Keywords are given half weight
                    keywordsMatched.add(word);
                }
            }

            // Check if the number of matches meets the minimum required
            if (matchCount >= config.minMatches) {
                matches.set(lang, matchCount);
            }
        }

        // Return the language with highest match count, or 'plaintext' if none
        if (matches.size > 0) {
            const sortedMatches = [...matches.entries()].sort((a, b) => b[1] - a[1]);
            const [bestMatch] = sortedMatches[0];
            return bestMatch;
        }

        return 'plaintext';
    }

    addCustomPatterns(language, patterns, keywords = [], minMatches = 3) {
        if (!this.languages.has(language)) {
            this.languages.set(language, {
                patterns: patterns,
                keywords: new Set(keywords),
                minMatches: minMatches
            });
        } else {
            const existing = this.languages.get(language);
            existing.patterns.push(...patterns);
            keywords.forEach(keyword => existing.keywords.add(keyword));
            if (minMatches) {
                existing.minMatches = minMatches;
            }
        }
    }
}

const detector = new LanguageDetector();
