import type { CardProps } from '@mui/material/Card';

import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import SvgIcon from '@mui/material/SvgIcon';
import { alpha } from '@mui/material/styles';
import Typography from '@mui/material/Typography';

// ----------------------------------------------------------------------

type StatusColor = 'primary' | 'success' | 'info' | 'warning' | 'error';

type Props = CardProps & {
  title: string;
  caption?: string;
  value: number;
  icon?: React.ReactNode;
  color?: StatusColor;
  prefix?: string;
};

export function StatusCard({
  title,
  caption,
  value,
  icon = <DefaultIcon />,
  color = 'primary',
  prefix,
  sx,
  ...other
}: Props) {
  return (
    <Card
      sx={[
        (theme) => ({
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
          p: 3,
          width: 225,
          borderRadius: 2,
          boxShadow: 'none',
          border: `1px solid ${theme.palette.divider}`,
        }),
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
      {...other}
    >
      <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <Box>
          <Typography variant="subtitle1" sx={{ color: 'text.primary' }}>
            {title}
          </Typography>

          {caption && (
            <Typography variant="body2" sx={{ color: 'text.disabled' }}>
              {caption}
            </Typography>
          )}
        </Box>

        <Box
          sx={(theme) => ({
            width: 48,
            height: 48,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            color: theme.palette[color].main,
            bgcolor: alpha(theme.palette[color].main, 0.16),
          })}
        >
          {icon}
        </Box>
      </Box>

      <Box sx={{ typography: 'h3' }}>
        {prefix}
        {value.toLocaleString()}
      </Box>
    </Card>
  );
}

// ----------------------------------------------------------------------

function DefaultIcon() {
  return (
    <SvgIcon>
      <path d="M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z" />
    </SvgIcon>
  );
}
