You are grading a Google Slides deck an assistant made for this request:

<request>
{request}
</request>

The deck: {deck}

Read it with browserd on the profile "personal": call session_start once, then open the deck. Go through every slide,
reading its text and looking at it in a screenshot. This is read-only: never type, click into a text box, drag, or
change anything in the deck, and never open Share. For the theme, Slide > Change theme lists the deck's own under "In
this presentation"; close the panel after. Close your tabs when you are done.

Files in your folder, for Read and Grep:
- flights-before.json and flights-after.json: Google Flights' nonstop LAX round trips for Nov 13 to 15, read just
  before and just after the assistant worked, as {"SEA": [[price, airline, departure, arrival], ...], "DEN": [...],
  "ORD": [...]}, cheapest first. Fares move by the minute, so a city's flights count if they match either file.
- seen.txt: the text of every page the assistant read, in order. Grep it to check the hotels.

Known values: the November average highs on the pinned Wikipedia pages are Seattle 52.1°F, Denver 52.9°F and Chicago
49.6°F. Rounding to a whole degree is fine.

Grade each item pass or fail, strictly but reasonably: wording, line order and number formatting do not matter;
values and structure do. Work the numbers out yourself from what the deck shows.

Correct:
- slides: 6 slides: a title slide, the recommendation, the comparison table, then a slide per city.
- seattle_flights, denver_flights, chicago_flights: the city's slide has a table of 3 nonstop flights, each with its
  airline, departure and arrival time and round-trip price; all 3 are in one flights file with that airline, those
  times and that price; and they are that file's 3 cheapest (any of several tied at one price will do).
- seattle_hotel, denver_hotel, chicago_hotel: the city's slide names a hotel and its nightly price, and seen.txt shows
  Google Hotels listing that hotel at that price as a 4-star hotel rated 4.0 or higher.
- seattle_weather, denver_weather, chicago_weather: the city's slide gives its November high, matching the value above.
- comparison: the comparison table has a row per city with its cheapest fare (the lowest of its 3 flights), the hotel
  for 2 nights (2 × nightly), the cost per person (fare + hotel for 2 nights) and the November high, all agreeing with
  the city slides.
- pick: the recommended city has the lowest cost per person by the deck's own numbers; a tie goes to the higher
  November high.
- recommendation: the recommendation slide gives the city, the cost per person, the team total (6 × per person), and
  the flight (airline and time) and hotel.

Polish:
- theme: the deck is in the Simple Light theme.
- photos: each city's slide has a photo, and the photo shows that city.
- headers: every table's header row has a dark fill and white bold text.
- highlight: the recommended city's row stands out in the comparison table.
- layout: on no slide does anything overlap, run off the slide, or get cut off.
- consistent: fonts, sizes and alignment are consistent across the slides and easy to read.

Then looks, 1 to 10: how good the deck looks as something to send a manager.

Your final message is only this JSON, every item above in it, each note one short sentence:
{"correct": {"slides": [true, "note"], "seattle_flights": [false, "note"], ...}, "polish": {"theme": [true, "note"],
...}, "looks": 7, "summary": "one or two sentences"}
