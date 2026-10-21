import type { CommonColors } from '@mui/material/styles';

import type { ThemeCssVariables } from './types';
import type { PaletteColorNoChannels } from './core/palette';

// ----------------------------------------------------------------------

type ThemeConfig = {
  classesPrefix: string;
  cssVariables: ThemeCssVariables;
  fontFamily: Record<'primary' | 'secondary', string>;
  palette: Record<
    'primary' | 'secondary' | 'info' | 'success' | 'warning' | 'error',
    PaletteColorNoChannels
  > & {
    common: Pick<CommonColors, 'black' | 'white'>;
    grey: Record<
      '50' | '100' | '200' | '300' | '400' | '500' | '600' | '700' | '800' | '900',
      string
    >;
  };
};

export const themeConfig: ThemeConfig = {
  /** **************************************
   * Base
   *************************************** */
  classesPrefix: 'minimal',
  /** **************************************
   * Typography
   *************************************** */
  fontFamily: {
    primary: 'DM Sans Variable',
    secondary: 'Barlow',
  },
  /** **************************************
   * Palette
   *************************************** */
 palette: {
    primary: {
      lighter: '#F6EFE6',
      light: '#E6D5BF',
      main: '#C9AE8C', // warm sand
      dark: '#A38663',
      darker: '#6F5A40',
      contrastText: '#3B2F22',
    },
    secondary: {
      lighter: '#F7E6E1',
      light: '#EBC5BC',
      main: '#D9A79C', // dusty blush
      dark: '#B07F74',
      darker: '#7A4F46',
      contrastText: '#3B2F22',
    },
    info: {
      lighter: '#E3EEF1',
      light: '#BCD6DD',
      main: '#8FB8C4', // soft sage blue
      dark: '#5F8E9B',
      darker: '#3A5C66',
      contrastText: '#24363B',
    },
    success: {
      lighter: '#E8F0DF',
      light: '#C8DDB4',
      main: '#A3C285', // soft sage green
      dark: '#789A57',
      darker: '#4A6335',
      contrastText: '#2B3A1D',
    },
    warning: {
      lighter: '#FBF1D9',
      light: '#F2DDA6',
      main: '#E6C46F', // butter
      dark: '#B8943F',
      darker: '#7A5F1F',
      contrastText: '#3B2F22',
    },
    error: {
      lighter: '#F9E1DC',
      light: '#EFB3A8',
      main: '#DE8574', // soft terracotta
      dark: '#B45C4B',
      darker: '#7A362A',
      contrastText: '#FFFFFF',
    },
    grey: {
      '50': '#FDFBF8',
      '100': '#FAF6F0',
      '200': '#F3EDE3',
      '300': '#E4DCCF',
      '400': '#CFC4B3',
      '500': '#A39683',
      '600': '#7D705E',
      '700': '#5A4F41',
      '800': '#3B3329',
      '900': '#261F18',
    },
    common: { black: '#000000', white: '#FFFFFF' },
  },
  /** **************************************
   * Css variables
   *************************************** */
  cssVariables: {
    cssVarPrefix: '',
    colorSchemeSelector: 'data-color-scheme',
  },
};
