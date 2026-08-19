import { google } from 'googleapis';
import { prisma } from '../src/lib/prisma';

const DOMAIN = process.argv[2] ?? 'purpledogwalkers.com';
const ROW_LIMIT = 25000;
const HISTORY_DAYS = 480;

function isoDateDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().split('T')[0];
}

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().split('T')[0];
}

async function main() {
  console.log(`[Discovery] domain=${DOMAIN}`);

  const site = await prisma.site.findFirst({
    where: {
      OR: [
        { url: DOMAIN },
        { url: `www.${DOMAIN}` },
        { siteId: `sc-domain:${DOMAIN}` },
        { siteId: `https://${DOMAIN}/` },
        { siteId: `https://www.${DOMAIN}/` },
      ],
    },
  });

  if (!site) {
    throw new Error(`No OpenGSC site found for ${DOMAIN}`);
  }

  console.log(`[Discovery] site.id=${site.id}`);
  console.log(`[Discovery] GSC property=${site.siteId}`);

  const accounts = await prisma.account.findMany({
    where: {
      userId: site.userId,
      provider: 'google',
    },
  });

  if (accounts.length === 0) {
    throw new Error('No Google OAuth account found');
  }

  const endDate = isoDateDaysAgo(2);
  const startDate = isoDateDaysAgo(HISTORY_DAYS + 2);

  let wm: ReturnType<typeof google.webmasters> | null = null;

  for (const account of accounts) {
    const oauth2 = new google.auth.OAuth2(
      process.env.GOOGLE_CLIENT_ID,
      process.env.GOOGLE_CLIENT_SECRET,
    );

    oauth2.setCredentials({
      access_token: account.access_token ?? undefined,
      refresh_token: account.refresh_token ?? undefined,
      expiry_date: account.expires_at
        ? account.expires_at * 1000
        : undefined,
    });

    const candidate = google.webmasters({
      version: 'v3',
      auth: oauth2,
    });

    try {
      await candidate.searchanalytics.query({
        siteUrl: site.siteId,
        requestBody: {
          startDate: endDate,
          endDate,
          dimensions: ['query'],
          rowLimit: 1,
        },
      });

      wm = candidate;
      console.log(
        `[Discovery] usable account=${account.providerAccountId}`,
      );
      break;
    } catch {
      // Try next Google account.
    }
  }

  if (!wm) {
    throw new Error(
      `No linked Google account can query ${site.siteId}`,
    );
  }

  await prisma.$executeRawUnsafe(`
    CREATE TABLE IF NOT EXISTS gsc_keyword_observation (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      site_id TEXT NOT NULL,
      site_property TEXT NOT NULL,
      date TEXT NOT NULL,
      query TEXT NOT NULL,
      page TEXT NOT NULL,
      search_type TEXT NOT NULL DEFAULT 'web',
      clicks REAL NOT NULL DEFAULT 0,
      impressions REAL NOT NULL DEFAULT 0,
      ctr REAL NOT NULL DEFAULT 0,
      position REAL NOT NULL DEFAULT 0,
      fetched_at TEXT NOT NULL,
      UNIQUE(site_id, date, query, page, search_type)
    )
  `);

  let day = startDate;
  let totalRows = 0;

  while (day <= endDate) {
    let startRow = 0;
    let dayRows = 0;

    while (true) {
      const response = await wm.searchanalytics.query({
        siteUrl: site.siteId,
        requestBody: {
          startDate: day,
          endDate: day,
          dimensions: ['query', 'page'],
          type: 'web',
          rowLimit: ROW_LIMIT,
          startRow,
          dataState: 'all',
        },
      });

      const rows = response.data.rows ?? [];

      if (rows.length === 0) {
        break;
      }

      const fetchedAt = new Date().toISOString();

      for (const row of rows) {
        const query = row.keys?.[0];
        const page = row.keys?.[1];

        if (!query || !page) continue;

        await prisma.$executeRaw`
          INSERT INTO gsc_keyword_observation (
            site_id,
            site_property,
            date,
            query,
            page,
            search_type,
            clicks,
            impressions,
            ctr,
            position,
            fetched_at
          )
          VALUES (
            ${site.id},
            ${site.siteId},
            ${day},
            ${query},
            ${page},
            ${'web'},
            ${row.clicks ?? 0},
            ${row.impressions ?? 0},
            ${row.ctr ?? 0},
            ${row.position ?? 0},
            ${fetchedAt}
          )
          ON CONFLICT(site_id, date, query, page, search_type)
          DO UPDATE SET
            clicks = excluded.clicks,
            impressions = excluded.impressions,
            ctr = excluded.ctr,
            position = excluded.position,
            fetched_at = excluded.fetched_at
        `;
      }

      dayRows += rows.length;
      totalRows += rows.length;

      console.log(
        `[Discovery] ${day} startRow=${startRow} rows=${rows.length}`,
      );

      if (rows.length < ROW_LIMIT) {
        break;
      }

      startRow += ROW_LIMIT;

      if (startRow >= 50000) {
        console.log(
          `[Discovery] ${day}: reached 50,000-row boundary`,
        );
        break;
      }
    }

    console.log(`[Discovery] ${day}: total=${dayRows}`);

    day = addDays(day, 1);
  }

  await prisma.$executeRawUnsafe(`DROP VIEW IF EXISTS gsc_page_keyword_summary`);

  await prisma.$executeRawUnsafe(`
    CREATE VIEW gsc_page_keyword_summary AS
    SELECT
      site_id,
      query,
      page,
      COUNT(*) AS observations,
      SUM(impressions) AS impressions,
      SUM(clicks) AS clicks,
      MIN(position) AS best_position,
      MAX(position) AS worst_position,
      AVG(position) AS avg_position,
      MIN(date) AS first_seen,
      MAX(date) AS last_seen
    FROM gsc_keyword_observation
    GROUP BY site_id, query, page;
  `);

  await prisma.$executeRawUnsafe(`DROP VIEW IF EXISTS gsc_page_keyword_latest`);

  await prisma.$executeRawUnsafe(`
    CREATE VIEW gsc_page_keyword_latest AS
    SELECT
      o.site_id,
      o.query,
      o.page,
      o.date,
      o.impressions,
      o.clicks,
      o.ctr,
      o.position
    FROM gsc_keyword_observation o
    INNER JOIN (
      SELECT
        site_id,
        query,
        page,
        MAX(date) AS latest_date
      FROM gsc_keyword_observation
      GROUP BY site_id, query, page
    ) latest
      ON o.site_id = latest.site_id
     AND o.query = latest.query
     AND o.page = latest.page
     AND o.date = latest.latest_date;
  `);

  await prisma.$executeRawUnsafe(`DROP VIEW IF EXISTS gsc_page_keyword_status`);

  await prisma.$executeRawUnsafe(`
    CREATE VIEW gsc_page_keyword_status AS
    SELECT
      l.*,
      CASE
        WHEN julianday('${endDate}') - julianday(l.date) <= 7 THEN 'active_7d'
        WHEN julianday('${endDate}') - julianday(l.date) <= 30 THEN 'active_30d'
        WHEN julianday('${endDate}') - julianday(l.date) <= 90 THEN 'stale_90d'
        ELSE 'historical'
      END AS status
    FROM gsc_page_keyword_latest l;
  `);

  await prisma.$executeRawUnsafe(`DROP VIEW IF EXISTS gsc_keyword_inventory`);

  await prisma.$executeRawUnsafe(`
    CREATE VIEW gsc_keyword_inventory AS
    SELECT
      s.site_id,
      s.query,
      s.page,
      s.observations,
      s.impressions,
      s.clicks,
      s.best_position,
      s.avg_position,
      s.worst_position,
      s.first_seen,
      s.last_seen,
      st.position AS latest_position,
      st.status
    FROM gsc_page_keyword_summary s
    LEFT JOIN gsc_page_keyword_status st
      ON s.site_id = st.site_id
     AND s.query = st.query
     AND s.page = st.page;
  `);

  const stats = await prisma.$queryRawUnsafe<any[]>(`
    SELECT
      COUNT(*) AS observations,
      COUNT(DISTINCT query) AS keywords,
      COUNT(DISTINCT page) AS pages,
      MIN(position) AS best_position,
      MAX(position) AS worst_position
    FROM gsc_keyword_observation
    WHERE site_id = ?
  `, site.id);

  console.log('');
  console.log('[Discovery] COMPLETE');
  console.log(`[Discovery] rows=${totalRows}`);
  console.log(stats[0]);
}

main()
  .catch((err) => {
    console.error('[Discovery] FAILED');
    console.error(err);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
