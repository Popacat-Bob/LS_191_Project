import { useState } from 'react';

import Menu from '@mui/material/Menu';
import Table from '@mui/material/Table';
import Paper from '@mui/material/Paper';
import SvgIcon from '@mui/material/SvgIcon';
import MenuItem from '@mui/material/MenuItem';
import TableRow from '@mui/material/TableRow';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import IconButton from '@mui/material/IconButton';
import TableContainer from '@mui/material/TableContainer';

import { OS_LABEL, OrderProps } from './utils';

// ----------------------------------------------------------------------

export type EditField = 'customer' | 'item' | 'amount' | 'status';

type Prop = {
  headerRows?: string[];
  rows?: OrderProps[];
  onEdit?: (order: OrderProps, field: EditField) => void;
  onRemove?: (order: OrderProps) => void;
};

export function OrderTable({
  headerRows = ['ID', 'Customer Name', 'Item Name', 'Amount', 'Status', 'Date'],
  rows = [],
  onEdit,
  onRemove,
}: Prop) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [selected, setSelected] = useState<OrderProps | null>(null);

  const openMenu = (event: React.MouseEvent<HTMLElement>, row: OrderProps) => {
    setAnchorEl(event.currentTarget);
    setSelected(row);
  };

  const closeMenu = () => setAnchorEl(null);

  const handleEdit = (field: EditField) => {
    if (selected) onEdit?.(selected, field);
    closeMenu();
  };

  const handleRemove = () => {
    if (selected) onRemove?.(selected);
    closeMenu();
  };

  return (
    <TableContainer component={Paper}>
      <Table sx={{ minWidth: 650 }} aria-label="order table">
        <TableHead>
          <TableRow>
            {headerRows.map((label) => (
              <TableCell key={label} align="left">
                {label}
              </TableCell>
            ))}
            <TableCell align="right" />
          </TableRow>
        </TableHead>

        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={row.id}
              sx={{ '&:last-child td, &:last-child th': { border: 0 } }}
            >
              <TableCell align="left">{row.id}</TableCell>
              <TableCell align="left">{row.customer}</TableCell>
              <TableCell align="left">{row.item}</TableCell>
              <TableCell align="left">{row.amount}</TableCell>
              <TableCell align="left">{OS_LABEL[row.status]}</TableCell>
              <TableCell align="left">{row.date}</TableCell>
              <TableCell align="right">
                <IconButton
                  aria-label={`actions for ${row.id}`}
                  onClick={(e) => openMenu(e, row)}
                >
                  <SvgIcon>
                    <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
                  </SvgIcon>
                </IconButton>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Menu anchorEl={anchorEl} open={!!anchorEl} onClose={closeMenu}>
        <MenuItem onClick={() => handleEdit('status')}>Edit status</MenuItem>
        <MenuItem onClick={() => handleEdit('amount')}>Edit amount</MenuItem>
        <MenuItem onClick={() => handleEdit('customer')}>Edit customer name</MenuItem>
        <MenuItem onClick={() => handleEdit('item')}>Edit item name</MenuItem>
        <MenuItem onClick={handleRemove} sx={{ color: 'error.main' }}>
          Remove
        </MenuItem>
      </Menu>
    </TableContainer>
  );
}
