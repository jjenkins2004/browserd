Plan a weekend away from Los Angeles: compare three cities on nonstop fares and November weather, pick one, and put it
in a short Google Slides deck.

Rules:
- Use browserd on the profile "personal": call session_start once, with the label "weekend trip".
- Use only the pages this task names, plus Google Flights and Google Slides. No search engines, no other sources.
- If a value is not where the task says, write "n/a". Never take it from anywhere else.
- Make exactly the slides listed, in this order, in the "Simple Light" theme. No speaker notes, no extra slides, no
  images or formatting beyond what is listed.
- If a page asks for a login, a captcha, 2FA or a payment, call tab_needs_input on that tab, then stop and say so.
- Never submit, book, buy, send or share anything.
- You are done when the end state below exists. Then stop: no review or polish pass. Your final message gives the URL
  of each file you made, then every value you entered. Close your session's tabs in one tab_close, except the deck's.

The cities, in this order:
  Seattle  SEA  https://en.wikipedia.org/w/index.php?oldid=1377723485
  Denver   DEN  https://en.wikipedia.org/w/index.php?oldid=1377063237
  Chicago  ORD  https://en.wikipedia.org/w/index.php?oldid=1377352940

For each city, one after another:
  1. On Google Flights (https://www.google.com/travel/flights?hl=en), search a round trip from LAX to the city's
     airport: depart Friday 2026-11-13, return Sunday 2026-11-15, 1 adult, economy, with Stops set to "Nonstop only".
     Choose the cheapest nonstop outbound flight, then the cheapest nonstop return flight. Record the airline, the
     outbound departure time, the return departure time, and the round-trip total in USD that the page shows once both
     are chosen. Stop there: never press a booking button or leave Google Flights.
  2. On the city's Wikipedia page above, in its Climate table, record November's "Mean daily maximum °F" as shown.

Google Slides deck "Weekend trip — Nov 13–15, 2026", 6 slides:
  1   Title: the deck's title. Subtitle: "From LAX, nonstop, 1 adult economy"
  2-4 One per city, in the order above. Title: "<City> (<airport>)". Body, 4 lines: the airline (both, as
      "<outbound> / <return>", when they differ), "Out <time> / Back <time>", "$<price> round trip",
      "Nov avg high <n>°F".
  5   Title: "Comparison". A table of 4 rows and 4 columns: a header row "City | Price | Airline | Nov high", then one
      row per city, in the order above, with the same values as its slide.
  6   Title: "Pick: <City>", the city with the lowest price; on a tie, the one with the higher Nov high. Body, 1 line:
      "$<price>, <n>°F avg high".

End state: the deck exists in My Drive, filled as above.
