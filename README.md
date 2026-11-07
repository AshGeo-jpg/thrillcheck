# Thrillcheck

Rates every NFL game from Must watch to Skip it, with no scores and no spoilers.

## What's in here

| File | What it does |
|---|---|
| `score_games.py` | Rates games from free nflverse play-by-play data |
| `opinions.py` | Optional: adds web buzz and Claude-written spoiler-free blurbs |
| `build_site.py` | Builds the website into `site/` |
| `template.html` | The site's design and app code |
| `config.json` | Donation link and goal, ad placeholders, replay links |
| `.github/workflows/update.yml` | Rebuilds and republishes the site every 3 hours, for free |

## Try it on your computer

```
pip install pandas pyarrow requests anthropic
python build_site.py --inline     # makes site/preview.html, open it in a browser
```

## Put it online (free, about 20 minutes)

1. Make a free GitHub account and a new repository called `thrillcheck`. Upload everything in this folder.
2. In the repository: **Settings > Pages > Source: GitHub Actions**.
3. **Actions tab > Update Thrillcheck > Run workflow.** Your site goes live at `https://<your-username>.github.io/thrillcheck/`.
4. From then on it updates itself every 3 hours.

Custom domain: use `thrillcheck.com`, add it under **Settings > Pages > Custom domain**, then set a repository variable `SITE_URL` to `https://thrillcheck.com`.


## How ratings work now

| League | First rating | Confirmed by |
|---|---|---|
| NFL, NBA | **Early rating** about an hour after the game, from match reports read by AI (`ai_rater.py`) | Play-by-play formula when the free data arrives (same night or next morning) |
| Premier League, La Liga (beta) | AI rating from match reports | Stays as is (no free play-by-play for soccer) |

Accuracy guard: the AI must find the same final score on at least two different websites and be at least 60% sure,
or the game is marked pending and retried an hour later. Every game is scored with the same checklist and formula.

`results/ai_check.csv` compares AI ratings with the play-by-play ratings for every NFL/NBA game that has both,
so you can see how often the AI is off by 20+ points.

### Keys to add (Settings > Secrets and variables > Actions)

| Secret | What for | Cost |
|---|---|---|
| `BRAVE_API_KEY` | Web search for match reports | $5 per 1,000 searches, with $5 free credit a month |
| `ANTHROPIC_API_KEY` | Claude reads the reports | A fraction of a cent per game on Haiku |
| `FOOTBALL_DATA_KEY` | Premier League and La Liga fixture lists | Free at football-data.org |

Optional variable `CLAUDE_MODEL`: set to `claude-sonnet-5-5` for sharper judgment at a higher cost.
Expected total in season: roughly $10 to $25 a month. The `--limit 40` setting caps games rated per run.

**Keep the GitHub repository public.** The workflow runs every 30 minutes, which is free and unlimited on public
repositories. Your keys stay private in Secrets either way.

## Turn on web buzz (optional, about $3 to $5 a season)

Add two repository secrets under **Settings > Secrets and variables > Actions**:
- `BRAVE_API_KEY` from api-dashboard.search.brave.com
- `ANTHROPIC_API_KEY` from console.anthropic.com

The workflow will then add buzz and blurbs to the 20 newest games on each run, and remember results so each game is only paid for once.

## Money settings

Edit `config.json`:
- `donateUrl`: your Ko-fi or Buy Me a Coffee page
- `donate.mode`: `"list"` at launch shows "What your support pays for" (edit `donate.paysFor`).
  Switch to `"stretch"` once regular tips come in: it shows a ladder of unlocks (`donate.stretchGoals`)
  with a progress bar to the NEXT unlock, so there is never a finished goal. Update `donate.raised` monthly.
- `donate.supporters`: number of supporters. "Backed by N fans" only appears once it reaches `showSupportersFrom` (10).
- `showAdPlaceholders`: set to `false` until Google AdSense approves the site, then paste the AdSense code where the `ad` placeholder is in `template.html`

## Turn on the feedback form (free, 5 minutes)

1. Make a free account at formspree.io and create a form. The free plan takes 50 messages a month and emails them to you.
2. Copy the form's endpoint, which looks like `https://formspree.io/f/abcd1234`.
3. Paste it into `config.json` as `"feedbackEndpoint"`. Until you do, the form explains that feedback isn't connected yet and sends nothing.

## Spoiler rules built in

- Page titles, link previews and blurbs never contain scores or winners.
- `build_site.py` refuses to build if any blurb contains a score or words like "won" or "beat".
- Final scores appear only after a viewer taps **Reveal score** and confirms.

## Pages and extras

- **About, Privacy policy, Contact** at `/about/`, `/privacy/`, `/contact/`. Google AdSense requires the privacy policy and a contact page.
  Put your email in `config.json` as `contactEmail`. The privacy policy is a plain-language starting point, not legal advice.
  Update it (and `policyDate`) if you add analytics or anything else that collects data.
- **Team pages** at `/teams/nfl-kc/` and similar, and **season rankings** at `/best/nfl-2026/`. Both are in the sitemap for Google.
- **Share images**: every game gets a spoiler-free 1200x630 card (`og.py`) that shows when its link is shared on X, Reddit, iMessage and others.
- **Add to home screen**: the site installs like an app on phones (manifest, icons, and an offline helper in `sw.js`).
- **I watched it**: saved on the visitor's device. Watched games reveal their score and move to a Watched section.

## Turn on fan votes ("Was it worth watching?"), free

Votes work on each visitor's device right away. To collect everyone's votes and show "86% of fans said it was worth it":

1. Make a free project at supabase.com.
2. Open the SQL editor and run:

```sql
create table votes (
  id bigserial primary key,
  game_id text not null check (char_length(game_id) < 80),
  worth boolean not null,
  created_at timestamptz default now()
);
alter table votes enable row level security;
create policy "anyone can vote" on votes for insert to anon with check (true);
create view vote_totals as
  select game_id, count(*) filter (where worth) as yes, count(*) filter (where not worth) as no
  from votes group by game_id;
grant select on vote_totals to anon;
```

3. In Project Settings > API, copy the Project URL and the anon public key into `config.json` under `votes`.

Totals only show once a game has at least 5 votes. Free Supabase projects pause after a week with no activity,
so this suits a site with regular visitors. The anon key is meant to be public; the policy above only allows adding votes.
