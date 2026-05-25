/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/static/html/**/*.html",
    "./app/static/js/**/*.js",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        void: '#030303',
        surface: '#0a0a0a',
        raised: '#121212',
      }
    }
  },
  plugins: [],
}
