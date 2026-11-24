import TextField from '@mui/material/TextField';

// ----------------------------------------------------------------------

type Props = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

export function OrderSearch({
  value,
  onChange,
  placeholder = 'Search by order ID, customer or item...',
}: Props) {
  return (
    <TextField
      fullWidth
      size="small"
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
