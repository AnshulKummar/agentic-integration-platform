/** @type {import('tailwindcss').Config} */
const v = (name) => `var(--${name})`
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: v('bg'),
        surface: v('surface'),
        'surface-2': v('surface-2'),
        border: v('border'),
        fg: v('text'),
        muted: v('muted'),
        accent: v('accent'),
        'accent-contrast': v('accent-contrast'),
        ok: v('ok'),
        warn: v('warn'),
        bad: v('bad'),
        agent: {
          user: v('agent-user'),
          llm: v('agent-llm'),
          kg: v('agent-kg'),
          mcp: v('agent-mcp'),
          governance: v('agent-governance'),
          context: v('agent-context'),
          external: v('agent-external'),
        },
        learned: { loop1: v('learned-loop1'), loop2: v('learned-loop2') },
      },
    },
  },
  plugins: [],
}
