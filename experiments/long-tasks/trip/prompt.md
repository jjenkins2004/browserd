Plan a two-night team offsite from Los Angeles for a team of 6: compare three cities on nonstop fares, a hotel and
November weather, pick one, and put it in a short Google Slides deck for the team lead to approve.

Browser: use browserd on the profile "personal": call session_start once, with the label "team offsite". If a page
asks for a login, a captcha, 2FA or a payment, call tab_needs_input on that tab, then stop and say so. When you are
done, close your session's tabs in one tab_close, except the deck's.

Rules:
- Use only the pages this task names, plus Google Flights, Google Hotels and Google Slides. No search engines, no other
  sources.
- If a value is not where the task says, write "n/a". Never take it from anywhere else.
- Make a new deck with exactly the slides listed, in this order, in the "Simple Light" theme. No speaker notes, no
  extra slides, no images or formatting beyond what is listed.
- Never submit, book, buy, send or share anything.
- You are done when the end state below exists. Then stop: no review or polish pass. Your final message gives the
  deck's URL, then every value you entered.

The cities, in this order:
  Seattle  SEA  https://en.wikipedia.org/w/index.php?oldid=1377723485
  Denver   DEN  https://en.wikipedia.org/w/index.php?oldid=1377063237
  Chicago  ORD  https://en.wikipedia.org/w/index.php?oldid=1377352940

For each city:
  1. Flights. On Google Flights (https://www.google.com/travel/flights?hl=en), search a round trip from LAX (the
     airport, not all of Los Angeles) to the city's airport: depart Friday 2026-11-13, return Sunday 2026-11-15,
     1 adult, economy. Set Stops to "Nonstop only" and sort by price. Stay on the results' default "Best" tab: the
     "Cheapest" tab adds third-party budget fares, which the company does not book. Record the 3 cheapest outbound
     flights as the list shows them: airline, departure time, arrival time, and the round-trip price beside each.
     Do not select a flight.
  2. Hotel. On Google Hotels (https://www.google.com/travel/hotels?hl=en), search the city: check in 2026-11-13, check
     out 2026-11-15, 1 traveler. Set Hotel class to 4-star only and Guest rating to 4.0+, then sort by lowest price.
     Record the first hotel in the list that is not marked sponsored: its name and the nightly price the list shows.
  3. Weather. On the city's Wikipedia page above, in its Climate table, record November's "Mean daily maximum °F" as
     shown.

Then work out, for each city:
  Hotel, 2 nights = 2 × the hotel's nightly price
  Per person      = its cheapest round-trip fare + Hotel, 2 nights
  Team total      = 6 × Per person
Write money in whole dollars, with a comma from $1,000 up.

Google Slides deck "Team offsite — Nov 13–15, 2026", 6 slides:
  1    Layout "Title slide". Title: the deck's title. Subtitle: "Seattle, Denver or Chicago, for 6 people from LAX"
  2    Layout "Title and body". Title: "Recommendation: <City>", the city with the lowest Per person; on a tie, the one
       with the higher Nov high. Body, 3 lines:
         "$<Per person> per person, $<Team total> for the team"
         "Fly <airline> at <departure time>, stay at <hotel>", with its cheapest flight
         "Nov avg high <n>°F"
  3    Layout "Title only". Title: "Options at a glance". A table of 4 rows and 5 columns: a header row
       "City | Cheapest fare | Hotel, 2 nights | Per person | Nov high", then one row per city, in the order above:
       "<City> | $<fare> | $<hotel, 2 nights> | $<per person> | <n>°F".
  4-6  One per city, in the order above. Layout "Title only". Title: "<City> (<airport>)". A table of 4 rows and 4
       columns: a header row "Airline | Departs | Arrives | Round trip", then its 3 cheapest flights, cheapest first:
       "<airline> | <departure time> | <arrival time> | $<price>". Under the table, a text box of 2 lines:
       "Hotel: <hotel>, $<nightly price>/night" and "Nov avg high <n>°F".

End state: the new deck exists in My Drive, filled as above.
