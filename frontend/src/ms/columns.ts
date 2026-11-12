/** Header tooltips shared by the Hits and Peaks tables. */

export const MATCH_TOOLTIP =
  'Match factor (0–1000): similarity of the whole measured spectrum to the library ' +
  'spectrum. Extra ions, e.g. from co-elution or background, lower it.'

export const REVERSE_MATCH_TOOLTIP =
  'Reverse match factor (0–1000): compares only the m/z values of the library ' +
  'spectrum and ignores other ions, so co-elution and background lower it less.'
