You are grading a Google Doc an assistant made for this request:

<request>
{request}
</request>

The Doc: {doc}

Files in your folder, for Read and Grep:
- doc.md: the Doc's text as Markdown, exported as you began: headings as #, tables as pipe tables, links as
  [text](url), images as ![][imageN] references. Read it for every value.
- doc.pdf: the Doc as it looks, exported at the same time. Read it whole (a page range will not open) for the polish
  items; if it will not open, look at the Doc in browserd instead, read-only.
- flights-before.json and flights-after.json: Google Flights' nonstop LAX round trips for Nov 13 to 15, read just
  before and just after the assistant worked. Each maps each city's airport ({airports}) to {"best": [...],
  "cheapest": [...]}: "best" is the default results and "cheapest" the Cheapest tab, each a list of [price, airline,
  departure, arrival], cheapest first. Fares move by the minute, so a city's flights count if they match any one of
  its four lists, or the nonstop LAX flights of a Google Flights result list for that city on those dates that
  seen.txt shows the assistant reading during the run (for a city with several airports, a list for all of them
  counts too).
- seen.txt: every tool call the assistant made, in order: each call's input on a line starting "> ", then the text it
  got back. Grep it to check what the assistant read. A "> " line is the assistant's own input, including everything
  it typed into the Doc: it shows what the assistant opened and did, never a value. A value counts as seen only in a
  result from the site itself. A harness that reads pages from screenshots leaves little text; then its inputs show
  what it opened, and you check the value yourself on the site.

To check something the files cannot show, use browserd on the profile "{profile}": call session_start once, then
open the site (Google Flights, Google Hotels, Google Maps, Yelp, Reddit, a guide or TikTok). This is read-only: never book, post, vote,
save, sign in or change anything, and never open the Doc to edit it. Close your tabs when you are done.

The key, for each city: its November mean daily maximum from its Wikipedia climate table (a value within 2°F passes;
the table's "Mean maximum" row is another figure), and its GSA FY2027 M&IE total, the city's own destination:
{key}

Check on a site only what seen.txt's results do not show, opening pages by URL (the Doc's links, the Maps URL below),
not by searching where a URL will do. Skip opening a source link when a "> " line opened that same URL and the
result after it shows what the item needs. Run several Greps in one message.

Grade each item pass or fail, strictly but reasonably: wording, layout choices and number formatting do not matter;
values and what the Doc holds do. Work the numbers out yourself from what the Doc shows. A hotel's price may be a
night's, as Google Hotels lists it, or both nights', with or without taxes, as long as the Doc says which.

Correct:
- structure: the recommendation, then the cost table, then a section per city; a title, intro, notes or sources
  around them are fine.
- <city>_flights, for each city: the city's section has a table of 3 nonstop round trips, each with its airline,
  times and fare; all 3 are in one of the city's lists (above, or one seen.txt shows) with that airline, departure
  time and price; and they are that list's 3 cheapest (any of several tied at one price will do). If the 3 share one
  outbound with different returns, it passes when that outbound is its list's cheapest and seen.txt shows those
  returns at the lowest round-trip prices after the assistant chose it.
- <city>_hotel: the section names a hotel and its price, and seen.txt shows Google Hotels listing it, not as an ad,
  at that price as a 4-star hotel rated 4.0 or higher, with no cheaper non-ad 4-star hotel rated 4.0+ in those results
  (or, on Google Hotels now for Nov 13 to 15, it is such a hotel within 10% of that price).
- <city>_mie: the section gives the key's M&IE for the city (or, where the hotel lies in another GSA destination,
  that one's), and seen.txt shows the assistant on gsa.gov's per diem rates for it.
- <city>_weather: the section gives a November high within 2°F of the key's, and seen.txt shows the assistant on a
  Wikipedia page for the city (its article or its "Climate of" article).
- <city>_spots: the section lists in a table at least 7 places to eat in the city center (downtown or a
  neighborhood next to it), each linked to its Yelp page (yelp.com/biz/...), each rated 4.5 or higher by Yelp from
  fewer than 500 reviews and not closed for good, as seen.txt shows (a Yelp search results page, or a Google result
  giving its Yelp rating and review count, counts). Judge the center from the neighborhood or address the Doc or
  seen.txt shows; open no page for it. Of those seen.txt does not show, open 3 (all, if fewer) on Yelp now (allowing
  0.1 lower and 10% more reviews) and fail the item if one fails; if Yelp blocks you, use the Yelp rating Google's
  search results show for it.
- <city>_dinner: the section picks one of its spots for the team dinner and gives its driving distance from the
  hotel; a Google Maps driving route from the hotel, as seen.txt shows or as
  google.com/maps/dir/?api=1&origin=<hotel>&destination=<spot>&travelmode=driving shows now, is within 20% or 1
  mile of that distance, whichever is more.
- <city>_backing: the section links, for its pick, a Reddit comment (or post) whose own text names the place and
  recommends it (a thread's link passes if the comment is in it), and either a food guide's article (such as Eater,
  The Infatuation, Time Out, Michelin, or a local paper or magazine) that features it or a TikTok video whose caption
  or location tag names it.
- costs: each city's cost per person is its cheapest fare, plus a room for 2 nights, plus 2.5 times its M&IE (the
  M&IE plus twice the first-and-last-day amount), by the Doc's own numbers, within $2.
- table: the cost table covers every city with at least its cost per person, cheapest first, agreeing with the city
  sections.
- pick: the recommended city has the lowest cost per person by the Doc's own numbers.
- recommendation: it gives the city, the cost per person, the team total (6 × per person), the outbound and return
  flights of that city's cheapest round trip (the outbound as its section gives it), its hotel and its dinner pick.
- return: the recommendation's return flight is a Sunday Nov 15 nonstop to LAX with its airline and departure and
  arrival times, and seen.txt shows it in Google Flights' returning flights after the assistant chose the outbound
  the recommendation names (or another tied with it at the cheapest fare), at the lowest round-trip price among those
  returns (any of several tied will do). If seen.txt holds no returning flights for it, choose that outbound on Google
  Flights now: it passes if the return is listed at, or within 10% of, the lowest price among its returns.
- vetting: the recommended city's dinner pick also links a TikTok video whose caption or location tag names it, a
  food guide's article that features it, and another article or video from a different outlet (press, a blog or a
  video; not Reddit, a review site, listing, map, delivery or booking page, nor the place's own site or channels)
  from the last 3 years, each naming the place. A sponsored or hosted post counts.
- by_hand: seen.txt's inputs show the Doc written through the editor: no HTML put on the clipboard and pasted, no
  Apps Script, Docs API or page script writing it, no Gemini.

Polish (headings from doc.md, the rest from doc.pdf):
- headings: each city's section starts with a heading in a heading style (a # line in doc.md), not just bold text.
- headers: every table's header row stands out from its body (a fill, bold or color).
- highlight: the recommended city's row stands out in the cost table.
- photos: each city's section has a photo of the city or of a place in it (a skyline, street, landmark, its hotel
  or its dinner spot), showing what it should.
- layout: nothing is cut off, overlaps or runs off the page; no table is broken (stray text, empty rows, misaligned
  columns); no placeholder text is left. A page break inside a section is fine.
- consistent: fonts, sizes, spacing and alignment are consistent across the sections and easy to read.

Then looks, 1 to 10: how good the Doc looks as something to send a manager.

Give your verdict as this JSON, every item above in it ({items}), each note one short sentence:
{"correct": {"structure": [true, "note"], "seattle_flights": [false, "note"], ...}, "polish": {"headings": [true,
"note"], ...}, "looks": 7, "summary": "one or two sentences"}
