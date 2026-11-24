import { useState, useEffect } from 'react';

import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import MenuItem from '@mui/material/MenuItem';
import TextField from '@mui/material/TextField';
import DialogTitle from '@mui/material/DialogTitle';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';

import { EditField } from './order-table';
import { OS_LABEL, OrderProps, OrderStatus } from './utils';

// ----------------------------------------------------------------------

const FIELD_LABEL: Record<EditField, string> = {
  customer: 'Customer name',
  item: 'Item name',
  amount: 'Amount',
  status: 'Status',
};

const STATUS_OPTIONS = [
  OrderStatus.Pending,
  OrderStatus.Processing,
  OrderStatus.Shipped,
  OrderStatus.Delivered,
  OrderStatus.Cancelled,
].map((value) => ({ value, label: OS_LABEL[value] }));

type Props = {
  open: boolean;
  order: OrderProps | null;
  field: EditField | null;
  onClose: () => void;
  onSubmit: (id: string, patch: Partial<Omit<OrderProps, 'id'>>) => void;
};

export function OrderEditDialog({ open, order, field, onClose, onSubmit }: Props) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  // Load the current value each time the dialog opens
  useEffect(() => {
    if (open && order && field) {
      setValue(String(order[field]));
      setError('');
    }
  }, [open, order, field]);

  if (!order || !field) return null;

  const handleSave = () => {
    if (field === 'status') {
      onSubmit(order.id, { status: Number(value) as OrderStatus });
    } else if (field === 'amount') {
      const amount = Number(value);
      if (!value.trim() || Number.isNaN(amount) || amount <= 0) {
        setError('Enter an amount greater than 0');
        return;
      }
      onSubmit(order.id, { amount });
    } else {
      const text = value.trim();
      if (!text) {
        setError(`${FIELD_LABEL[field]} is required`);
        return;
      }
      if (field === 'customer') onSubmit(order.id, { customer: text });
      else onSubmit(order.id, { item: text });
    }
    onClose();
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>
        Edit {FIELD_LABEL[field].toLowerCase()} ({order.id})
      </DialogTitle>

      <DialogContent sx={{ pt: '8px !important' }}>
        {field === 'status' ? (
          <TextField
            select
            label="Status"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            fullWidth
          >
            {STATUS_OPTIONS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>
        ) : (
          <TextField
            autoFocus
            label={FIELD_LABEL[field]}
            type={field === 'amount' ? 'number' : 'text'}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError('');
            }}
            error={!!error}
            helperText={error}
            slotProps={field === 'amount' ? { htmlInput: { min: 0, step: '0.01' } } : undefined}
            fullWidth
          />
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSave}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
