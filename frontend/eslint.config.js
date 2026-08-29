import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  {
    // SettingsPanel 是「配置结构」驱动的动态表单：路径取值/设值、状态里放的都是
    // 后端 schema 传来的任意结构，显式 any 是这里的有意取舍，不是类型疏漏。
    files: ['src/components/SettingsPanel.tsx'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
)
