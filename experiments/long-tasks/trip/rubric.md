You are grading a Google Slides deck an assistant made for this request:

<request>
{request}
</request>

The deck: {deck}

Read it with browserd on the profile "personal": call session_start once, then open the deck. Go through every slide,
reading its text and looking at it in a screenshot. This is read-only: never type, click into a text box, drag, or
change anything in the deck, and never open Share. Close your tabs when you are done.

Files in your folder, for Read and Grep:
- flights-before.json and flights-after.json: Google Flights' nonstop LAX round trips for Nov 13 to 15, read just
  before and just after the assistant worked. Each is {"SEA": {"best": [...], "cheapest": [...]}, "DEN": {...},
  "ORD": {...}}: "best" is the default results and "cheapest" the Cheapest tab, each a list of [price, airline,
  departure, arrival], cheapest first. Fares move by the minute, so a city's flights count if they match any one of
  its four lists.
- seen.txt: the text of every page the assistant read, in order. Grep it to check the hotels.

The normal November highs are Seattle 52.1°F, Denver 52.9°F and Chicago 49.6°F. The assistant could use any source,
so a value within 3°F passes.

Grade each item pass or fail, strictly but reasonably: wording, layout choices and number formatting do not matter;
values and what the deck holds do. Work the numbers out yourself from what the deck shows.

Correct:
- slides: 6 slides: a title slide, the recommendation, the comparison, then a slide per city.
- seattle_flights, denver_flights, chicago_flights: the city's slide has a table of 3 nonstop round trips, each with
  its airline, times and price; all 3 are in one list above with that airline, departure time and price; and they are
  that list's 3 cheapest (any of several tied at one price will do).
- seattle_hotel, denver_hotel, chicago_hotel: the city's slide names a hotel and its price (a night, or for both
  nights), and seen.txt shows Google Hotels listing that hotel at that price as a 4-star hotel rated 4.0 or higher.
- seattle_weather, denver_weather, chicago_weather: the city's slide gives a November high within 3°F of the normal.
- comparison: the comparison table covers all three cities with at least each one's cost per person, agreeing with
  the city slides.
- pick: the recommended city has the lowest cost per person (its cheapest fare plus 2 nights at its hotel) by the
  deck's own numbers.
- recommendation: the recommendation slide gives the city, the cost per person, the team total (6 × per person), and
  the flight and hotel.

Polish:
- photos: each city's slide has a photo, and the photo shows that city.
- headers: every table's header row stands out from its body (a fill, bold or color).
- highlight: the recommended city stands out in the comparison table.
- layout: on no slide does anything overlap, run off the slide, or get cut off.
- consistent: fonts, sizes and alignment are consistent across the slides and easy to read.

Then looks, 1 to 10: how good the deck looks as something to send a manager.

Your final message is only this JSON, every item above in it, each note one short sentence:
{"correct": {"slides": [true, "note"], "seattle_flights": [false, "note"], ...}, "polish": {"photos": [true, "note"],
...}, "looks": 7, "summary": "one or two sentences"}
