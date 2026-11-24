import type { SelectChangeEvent } from '@mui/material/Select';

import Box from '@mui/material/Box';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import InputLabel from '@mui/material/InputLabel';
import FormControl from '@mui/material/FormControl';

import { FS_LABEL } from './utils';

import type { FilterProps } from './utils';

// ----------------------------------------------------------------------

type Prop = {
  labelId: string;
  id: string;
  value: number;
  label?: string;
  onChange: (event: SelectChangeEvent<number>) => void;
  options: FilterProps[];
};

export function FilterSelect({ labelId, id, value, label = 'Status', onChange, options }: Prop) {
  return (
    <Box sx={{ minWidth: 120 }}>
      <FormControl fullWidth>
        <InputLabel id={labelId}>{label}</InputLabel>

        <Select<number>
          labelId={labelId}
          id={id}
          value={value}
          label={label}
          onChange={onChange}
          size="small"
        >
          {options.map((option) => (
            <MenuItem key={option.status} value={option.status}>
              {FS_LABEL[option.status]}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    </Box>
  );
}
