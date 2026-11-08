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
4. From then on it updates itself every 30 to 60 minutes.

Custom domain: use `thrillcheck.com`, add it under **Settings > Pages > Custom domain**, then set a repository variable `SITE_URL` to `https://thrillcheck.com`.


## How ratings work now (free, no keys)

| League | Where the rating comes from | How fast |
|---|---|---|
| NFL | ESPN play-by-play with ESPN's win probability (`espn.py`), backed up by nflverse | Within about an hour of the final whistle |
| NBA | ESPN play-by-play (`espn.py`), backed up by hoopR | Within about an hour |
| Premier League, La Liga (beta) | ESPN goal and red-card timing (`espn.py`) | Within about an hour |

NFL and NBA games get the same formula whichever source they come from: total swing in win probability,
late drama, how close the winner came to losing, final margin, lead changes and overtime, each turned into a
percentile against every other game. When nflverse or hoopR posts a game, that copy is used and ESPN's is
dropped. If ESPN ever stops working, nflverse and hoopR still rate NFL and NBA games, just a day or so later.

Soccer uses goal timing: late goals that change the result, total goals, final margin, how many times the
result flipped, comebacks, red cards and stoppage-time drama. Each match is compared with every match in the
same league this season and last season. Own goals are checked against the final score.

ESPN does not officially offer this feed to the public, so it could change or stop. `espn.py` fails soft:
a broken request is skipped and the site still builds.

`ai_rater.py` is the older AI rater (web search plus Claude). It is no longer part of the workflow but is kept in
case ESPN ever stops working for soccer.

**Keep the GitHub repository public.** The workflow runs every 30 minutes, which is free and unlimited on public
repositories.

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
