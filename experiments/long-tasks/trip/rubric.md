You are grading a Google Slides deck and the Google Sheet behind it, which an assistant made for this request:

<request>
{request}
</request>

The deck: {deck}
The Sheet: {sheet}

Read them with browserd on the profile "{profile}": call session_start once, then open each. Go through every slide,
reading its text and looking at it in a screenshot, and read the Sheet's cells and formulas. This is read-only: you may
select a cell or turn on View > Show formulas to read a formula, but never type, drag, or change anything in either
file, and never open Share. Close your tabs when you are done.

Files in your folder, for Read and Grep:
- flights-before.json and flights-after.json: Google Flights' nonstop LAX round trips for Nov 13 to 15, read just
  before and just after the assistant worked. Each maps each city's airport ({airports}) to {"best": [...],
  "cheapest": [...]}: "best" is the default results and "cheapest" the Cheapest tab, each a list of [price, airline,
  departure, arrival], cheapest first. Fares move by the minute, so a city's flights count if they match any one of
  its four lists, or a Google Flights result list for that city that seen.txt shows the assistant reading during the
  run (a long run sees fares the two reads miss).
- seen.txt: the text of every page the assistant read, in order. Grep it to check the hotels and restaurants. If it
  holds no text for one (the assistant may have read it from a screenshot), check it yourself on Google Hotels or
  Google Maps, in the same read-only way.

The normal November highs, from each city's Wikipedia climate table, are {normals}. A value within 2°F passes.

Grade each item pass or fail, strictly but reasonably: wording, layout choices and number formatting do not matter;
values and what the files hold do. Work the numbers out yourself from what the deck shows. A hotel's price may be a
night's, as Google Hotels lists it, or both nights'.

Correct:
- slides: a slide per city plus 5: a title slide, the recommendation, the comparison table, the chart, the city
  slides, then the itinerary.
- <city>_flights, for each city: the city's slide has a table of 3 nonstop round trips, each with its airline, times
  and price; all 3 are in one of the city's lists (above, or one seen.txt shows) with that airline, departure time
  and price; and they are that list's 3 cheapest (any of several tied at one price will do).
- <city>_hotel: the city's slide names a hotel and its price, and seen.txt shows that hotel at that price as a 4-star
  hotel rated 4.0 or higher (or, checked on Google Hotels now, a 4-star hotel rated 4.0+ within 10% of that price).
- <city>_weather: the city's slide gives a November high within 2°F of the normal.
- <city>_restaurant: the city's slide names a restaurant and its walking time from the hotel; it is rated 4.5 or
  higher on Google Maps, and a walking route from that hotel takes about that time (within 3 minutes), as seen.txt
  or Google Maps now shows.
- sheet: the Sheet holds each city's fare, hotel and cost per person, the costs as formulas, agreeing with the deck.
- chart: the chart slide holds a bar or column chart of each city's cost per person, from the Sheet, its values
  agreeing with the comparison table.
- comparison: the comparison table covers every city with at least each one's cost per person, agreeing with
  the city slides.
- pick: the recommended city has the lowest cost per person (its cheapest fare plus 2 nights at its hotel) by the
  deck's own numbers.
- recommendation: the recommendation slide gives the city, the cost per person, the team total (6 × per person), and
  the flight, hotel and restaurant.
- itinerary: the last slide gives the recommended city's round trip, the one the recommendation names: the outbound
  leg's airline and departure and arrival times as the Flights lists above have them, and the return leg's on Sunday
  Nov 15, as seen.txt (or Google Flights now, choosing that outbound flight) shows them for that round trip.

Polish:
- photos: each city's slide has a photo of the city and one of its restaurant (its food, room or front), each
  showing what it should.
- headers: every table's header row stands out from its body (a fill, bold or color).
- highlight: the recommended city stands out in the comparison table and in the chart.
- layout: on no slide does anything overlap, run off the slide, or get cut off.
- consistent: fonts, sizes and alignment are consistent across the slides and easy to read.

Then looks, 1 to 10: how good the deck looks as something to send a manager.

Your final message is only this JSON, every item above in it ({items}), each note one short sentence:
{"correct": {"slides": [true, "note"], "seattle_flights": [false, "note"], ...}, "polish": {"photos": [true, "note"],
...}, "looks": 7, "summary": "one or two sentences"}
