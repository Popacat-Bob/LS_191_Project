import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';

import { DashboardContent } from 'src/layouts/dashboard';
import { _posts, _tasks, _traffic, _timeline } from 'src/_mock';

import { AnalyticsNews } from '../analytics-news';
import { AnalyticsTasks } from '../analytics-tasks';
import { StatusCard } from '../analytics-status-card';
import { AnalyticsCurrentVisits } from '../analytics-current-visits';
import { AnalyticsOrderTimeline } from '../analytics-order-timeline';
import { AnalyticsWebsiteVisits } from '../analytics-website-visits';
import { AnalyticsWidgetSummary } from '../analytics-widget-summary';
import { AnalyticsTrafficBySite } from '../analytics-traffic-by-site';
import { AnalyticsCurrentSubject } from '../analytics-current-subject';
import { AnalyticsConversionRates } from '../analytics-conversion-rates';
import { AnalyticsSalesChart, ChartData } from '../analytics-sales-chart';

// ----------------------------------------------------------------------

const salesData: ChartData = {
  type: 'bar',
  categories: ['2017', '2018', '2019', '2020'],
  series: [
    { name: 'Online', data: [45, 27, 30, 27] },
    { name: 'In-store', data: [37, 30, 56, 35] },
    { name: 'Wholesale', data: [33, 33, 40, 40] },
  ],
};

const visitsData: ChartData = {
  type: 'line',
  categories: ['2017', '2018', '2019', '2020'],
  series: [
    { name: 'Visits', data: [900, 1200, 1500, 1400] },
  ],
};

export function OverviewAnalyticsView() {
  return (
    <DashboardContent maxWidth="xl">
      <Grid container spacing={3}>
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <StatusCard
            title="Pending Orders"
            caption="A number of pending orders."
            value={100} // TODO: fetcher for pending orders
          />
        </Grid>

        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <StatusCard
            title="Total Revenue"
            caption="A number of sales this month."
            value={100} // TODO: fetcher and formatter for sales
          />
        </Grid>

        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <StatusCard
            title="Total Customers"
            caption="A number of active customers this month."
            value={100} // TODO: fetcher
          />
        </Grid>

        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <StatusCard
            title="Total Deliveries"
            caption="A number of deliveries made this month."
            value={100} // TODO: fetcher
          />
        </Grid>

        <Grid size={12}>
          <AnalyticsSalesChart
            metrics={[
              {
                key: 'sales',
                label: 'Sales',
                data: { day: salesData, week: salesData, month: salesData, year: salesData },
              },
              {
                key: 'visits',
                label: 'Visits',
                data: { day: visitsData, week: visitsData, month: visitsData, year: visitsData },
              },
            ]}
          />
        </Grid>
      </Grid>
    </DashboardContent>
  );
}
