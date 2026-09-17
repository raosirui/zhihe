/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#0a1f1a',
        pine: '#123b30',
        mint: '#b9f36a',
        paper: '#f6f8f3',
      },
      fontFamily: { sans: ['Inter', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'] },
      boxShadow: { soft: '0 12px 40px rgba(12, 42, 34, .08)', glow: '0 0 40px rgba(185, 243, 106, .18)' },
      animation: { float: 'float 6s ease-in-out infinite', enter: 'enter .45s ease-out both' },
      keyframes: {
        float: { '0%, 100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-8px)' } },
        enter: { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
      },
    },
  },
  plugins: [],
}

