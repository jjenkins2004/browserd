Compare the four hyperscalers' capital spending from their latest annual reports, in a Google Sheet and a short Google
Slides deck.

Rules:
- Use browserd on the profile "personal": call session_start once, with the label "hyperscaler capex".
- Use only the pages this task names, plus Google Sheets and Google Slides. No search engines, no other sources.
- If a value is not where the task says, write "n/a". Never take it from anywhere else.
- Make exactly the slides listed, in this order, in the "Simple Light" theme. No speaker notes, no extra slides, no
  images or formatting beyond what is listed.
- If a page asks for a login, a captcha, 2FA or a payment, call tab_needs_input on that tab, then stop and say so.
- Never submit, buy, send or share anything.
- You are done when the end state below exists. Then stop: no review or polish pass. Your final message gives the URL
  of each file you made, then every value you entered. Close your session's tabs in one tab_close, except the deck's.

Read these four 10-K filings, and only these:
  MSFT  https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm
  GOOGL https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm
  AMZN  https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm
  META  https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm
From each, take the two most recent fiscal years, in $ millions as printed:
  Revenue: the top revenue line of the Consolidated Statements of Income (AMZN: "Total net sales")
  Net income: "Net income" on the same statement
  Capex: on the Consolidated Statements of Cash Flows, MSFT's "Additions to property and equipment", the others'
         "Purchases of property and equipment" (gross; leave out any proceeds line)

Google Sheet "Hyperscaler capex", one tab. Row 1 holds these headers, in columns A to H:
  Company | Revenue prior | Revenue latest | Net income prior | Net income latest | Capex prior | Capex latest | Capex growth %
Rows 2 to 5 are MSFT, GOOGL, AMZN and META, with the values in $ millions as plain numbers. Column H is a formula,
=G2/F2-1 and so on, formatted as a percent with 1 decimal. Then add a column chart of columns A, F and G (capex prior
and latest for each company) to the same tab.

Google Slides deck "Hyperscaler capex — latest 10-Ks", 6 slides:
  1   Title: the deck's title. Subtitle: "Source: SEC EDGAR 10-K filings"
  2-5 One per company, in the order above. Title: "<ticker> — FY<the year its latest fiscal year ends>". Body, 3 lines:
      "Revenue: $X.XB (prior $Y.YB)", "Net income: $X.XB (prior $Y.YB)", "Capex: $X.XB (prior $Y.YB)", in $ billions
      rounded to 1 decimal, half up.
  6   Title: "Capex, prior vs latest". The Sheet's chart, inserted from Sheets (linked).

End state: the Sheet and the deck exist in My Drive, filled as above.
