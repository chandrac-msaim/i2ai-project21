/** Design tokens: docs/UI_SPEC.md section 1. Use only these colors. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: '#0e5a5c', dark: '#0b3b3d', soft: '#e2efee' },
        page: '#f3f4f6',
        card: '#ffffff',
        border: '#e6e8ec',
        ink: '#121519',
        muted: '#5f6670',
        info: { bg: '#e3edfb', border: '#d5e2f7' },
        success: { bg: '#e2f3e9', text: '#1f6b3a' },
        warning: { bg: '#fdf1dc', text: '#8a5a10' },
        danger: { bg: '#f6dede', text: '#a32d2d' },
        changed: { bg: '#fdf6e7' },
        accent: { 'blue-bg': '#e3edfb', 'blue-text': '#1d4f91' },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { card: '12px', control: '8px' },
      maxWidth: { page: '1320px' },
    },
  },
  plugins: [],
}
