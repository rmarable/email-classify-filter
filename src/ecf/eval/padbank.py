"""Padding for long cards (window tuning step 2, branch 3; OD-555): routine paragraphs in three
voices, so a long email reads like ordinary correspondence from its kind of sender. A card picks
one with `pad_voice`. Editing a paragraph changes every long card in that voice that uses it, and
so the set's version: tests pin the bank's digest so an edit is deliberate."""

from __future__ import annotations

# a supplier or service firm writing to a customer
VENDOR = (
    "Thanks again for the update on the spring order. The warehouse confirmed that the pallets"
    " arrived in good condition and the count matched the packing list, so nothing further is"
    " needed on our side for that delivery.",
    "As discussed on the call, the team will keep the current schedule for the quarterly review."
    " If anything changes we will share a revised agenda at least a week ahead so everyone has"
    " time to prepare their notes.",
    "A quick note on the shipping labels: the new format puts the order reference above the"
    " address block. Older labels are still accepted, but please switch when you next reprint a"
    " batch.",
    "Our office will be closed on the public holiday next month. Orders placed during the closure"
    " will be picked and packed on the first working day after it, in the order they came in.",
    "The product sheets you asked about are being updated with the new dimensions. We expect to"
    " share the final versions once the design team has checked the drawings against the samples.",
    "Following last week's meeting, the facilities group agreed to move the loading bay"
    " deliveries to the morning slot. Drivers have been told, and the gate staff have the updated"
    " rota.",
    "We have reviewed the feedback from the site visit. Most of the points were minor and have"
    " already been handled, and the remaining two are on the list for the next maintenance"
    " window.",
    "For your records, the contract summary we keep on file still lists the same contacts as"
    " last year. Let us know if anyone has joined or left your team so we can keep it current.",
    "The training session for the new ordering screens went well. A recording and a short guide"
    " will be shared with the attendees, and we can run another session if more people need it.",
    "Stock levels for the standard items look healthy for the season. The seasonal range is"
    " selling a little faster than planned, so we have brought forward the next production run.",
    "Thank you for the photos of the damaged carton. We have passed them to the carrier and will"
    " let you know what they say, though the goods inside appear to have been unaffected.",
    "The sustainability report is nearly finished. It covers packaging, transport and energy use"
    " at both sites, and we will circulate a draft to partners before it is published.",
    "Just a reminder that the annual stock count takes place at the end of the month. Deliveries"
    " can still be received that week, but collections may be slower than usual.",
    "Our customer service hours are unchanged. The team can be reached during the working day,"
    " and messages that arrive outside those hours are answered the next morning.",
    "We have added two new colours to the catalogue in response to requests from several"
    " customers. Samples are available on request, and the full range will be shown at the trade"
    " fair.",
    "The engineering team completed the routine checks on the packing line. No faults were"
    " found, and the line is running at its normal speed again after the short planned stop.",
    "Your new contact on our sales team will introduce themselves next week. They have worked"
    " with similar customers for several years and will keep the same regular check-in that you"
    " have now.",
    "The revised care instructions are now printed inside each box lid. The wording is shorter"
    " and easier to follow, and the diagrams match the current version of the product.",
    "We noticed the delivery address on your last few orders has a different unit number. The"
    " driver found you without trouble, but it would help to have the full address on the order.",
    "The new storage racks in our main warehouse are now in use. They give us room for about a"
    " fifth more stock, which should shorten the wait on the items that sold out last winter.",
    "Our quality team ran the usual spot checks on the latest batch. Everything was within the"
    " agreed tolerances, and the test results are kept on file should you ever want to see them.",
    "Some customers have asked whether the outer cartons can be recycled. They can, and from next"
    " month the cartons will carry a small printed note saying so on the side panel.",
    "We are trialling a lighter pallet wrap that uses less plastic. If you see the new wrap on a"
    " delivery and notice any problem with how the load arrived, please let us know.",
    "The holiday rota for our support desk is confirmed. There will always be someone on duty"
    " during working hours, though replies may take a little longer in the busiest week.",
    "Thanks for taking part in the customer survey. The most common request was clearer delivery"
    " windows, and we are working with the carriers on a narrower time slot for each drop.",
    "The trade fair stand went well this year. We met several of our regular customers in person"
    " and took notes on the ideas they shared, which the product team will look at this quarter.",
    "Our van fleet is being replaced over the next few months. The new vans are quieter and"
    " cleaner, and the drivers will keep their usual routes during the changeover.",
    "We have updated the measurements in the online catalogue for the larger shelving units. The"
    " products themselves have not changed; the earlier figures were rounded too far.",
    "As you know, the warehouse team works two shifts. From next month the second shift will"
    " start an hour earlier, which should mean evening collections are ready sooner.",
    "The returns process is unchanged: items come back in their original packaging with the"
    " returns slip, and the warehouse checks them within a few working days of arrival.",
    "We have restocked the spare parts that were short in the autumn. Hinges, brackets and the"
    " small fixing kits are all back on the shelves and can be added to your next order.",
    "One of our longest serving drivers retires at the end of the month. Many of you will know"
    " him from the morning rounds, and his colleagues are planning a small farewell.",
    "The packaging supplier has moved to a new site closer to our warehouse. Lead times on boxed"
    " items may improve slightly once the move has settled down over the coming weeks.",
    "We tested the new labels in cold storage and they stay readable at low temperatures. That"
    " was a concern raised at the last review, so it is good to have the result on record.",
    "Our website now shows estimated delivery days for each region. The estimates come from the"
    " carriers and are usually accurate, though bad weather can still cause the odd delay.",
    "The new pallet jacks arrived and the team has been trained on them. Loading is a little"
    " quicker now, which helps on days when several collections are booked close together.",
    "Thank you for the kind note about the driver who helped unload during the storm. We have"
    " passed your thanks on, and he was pleased to hear that the delivery made a difference.",
    "The product photographs on the catalogue pages have been retaken in natural light. The"
    " colours now look closer to the real items, which several customers had asked us about.",
    "We have extended the warranty on the heavier units from one year to two. Nothing changes in"
    " how claims are handled; the longer period simply applies to orders from this month.",
    "Our purchasing team visited the timber yard that supplies the frames. The yard follows the"
    " forestry standards we agreed on, and the visit report is filed with our quality records.",
    "The office move on our side is finished. Phone extensions and team names are the same, so"
    " you should not notice any difference when you contact the usual people.",
    "Next quarter we will review the range of smaller items. A few slow sellers may be retired,"
    " and we will give plenty of notice before anything you order regularly is changed.",
    "The assembly guides are now available in larger print. They were redesigned after feedback"
    " from a care home that orders from us, and the new layout is easier for everyone to read.",
    "Our recycling partner collects used pallets each week. If you have any spare pallets from"
    " earlier deliveries, the driver can take them back on the next visit at no extra charge.",
)

# a colleague writing inside the organisation
INTERNAL = (
    "Quick reminder that the kitchen on the third floor will be closed on Thursday for its deep"
    " clean. The second floor kitchen stays open, and the coffee machine there was serviced last"
    " week.",
    "The monthly all-hands moves to the large meeting room this time, as the usual room is being"
    " repainted. The agenda is the same, with a short update from each team at the end.",
    "Facilities have asked everyone to clear personal items from the hot desks by Friday"
    " evening. Anything left behind will be boxed up and kept at reception for a week.",
    "The new starters' welcome lunch is on Tuesday. If you are mentoring someone this quarter,"
    " please come along and introduce yourself; it makes the first week much easier for them.",
    "The fire drill planned for this week has been moved to next Wednesday morning. Please make"
    " sure you know your nearest exit and the assembly point in the car park.",
    "Thanks to everyone who helped with the office move. The last of the boxes have been"
    " unpacked, and the spare chairs are stored in the room next to the lifts for now.",
    "The printer on the second floor is working again after the technician's visit. If it jams,"
    " please leave a note on it rather than opening the back panel yourself.",
    "The team offsite is booked for the end of next month. We will spend the morning on planning"
    " and the afternoon on a walk along the river, weather permitting.",
    "A reminder that the quarterly reviews start next week. Your manager will book a time with"
    " you; there is nothing to prepare beyond a few notes on what went well and what did not.",
    "The car park barrier is being replaced over the weekend. On Monday, please use the side"
    " entrance and allow a few extra minutes, as there may be a short queue.",
    "The book club meets again on the last Friday of the month. This time we are reading a short"
    " history of the city, and new members are always welcome to join in.",
    "The heating in the east wing has been adjusted after several people mentioned the cold"
    " mornings. Let facilities know if it is still uncomfortable later in the week.",
    "The intranet will be offline for maintenance on Saturday night. Nothing is needed from you,"
    " and everything should be back to normal well before the start of the working week.",
    "We are collecting ideas for the summer social. So far the favourites are a picnic in the"
    " park and a boat trip, and there is a box at reception for any other suggestions.",
    "The cycle storage behind the building now has more racks and a covered area. Please use the"
    " racks rather than locking bikes to the railings by the entrance.",
    "The meeting room screens have been upgraded. If you have trouble connecting a laptop, the"
    " short guide pinned by each screen covers the usual problems.",
    "A gentle reminder to book holiday leave for the summer in the shared calendar. It helps the"
    " team leads plan cover, especially for the weeks when the schools are out.",
    "The charity bake sale raised a good amount for the local food bank. Thank you to everyone"
    " who baked or bought something, and to the reception team for running the table.",
    "The new desk lamps have arrived and are being handed out by facilities. They are adjustable"
    " and much brighter than the old ones, which should help on the darker afternoons.",
    "The project retrospective is set for Thursday afternoon. We will look at what worked, what"
    " slowed us down and what to keep for the next phase, in about an hour.",
    "The lift in the north stairwell is out of service until Wednesday for its inspection. The"
    " other lift is working normally, and the stairs are open as usual.",
    "Please remember to lock your screen when you step away from your desk. It is a small habit,"
    " but it keeps our work private in a shared office.",
    "The wellbeing session on managing stress is on Monday at lunchtime. It is optional and"
    " informal, and the slides will be shared afterwards for anyone who cannot attend.",
    "We have a new recycling system for the office. Paper, cans and food waste each have their"
    " own bins now, and the posters above them show what goes where.",
    "The team photo for the annual report will be taken next Tuesday morning. Smart casual is"
    " fine, and it should only take a few minutes of everyone's time.",
    "The visitors' parking spaces are reserved for guests only. If you are in early, please park"
    " in the main bays so there is space for the people we are expecting.",
    "Our department newsletter is looking for short pieces from each team. A few lines about a"
    " recent project or a team member's achievement would be ideal.",
    "The cleaning schedule has changed slightly. The cleaners now come in the early evening"
    " rather than the morning, so please leave desks reasonably tidy at the end of the day.",
    "The garden on the roof terrace is open again now that the weather is warmer. It is a quiet"
    " place for lunch, and the gardening group is always glad of extra help.",
    "The new phone handsets will be installed over the next two weeks. Your extension number"
    " stays the same, and facilities will let each team know when their turn comes.",
    "Thank you to everyone who filled in the staff survey. The results will be shared at the next"
    " all-hands, along with what we plan to change in response.",
    "The first aid training course has a few places left. It runs over two mornings, and those"
    " who complete it will be added to the list of first aiders on each floor.",
    "The office will close early on the day before the holiday weekend. Please plan your work so"
    " that nothing important is left for the final afternoon.",
    "The quiet room on the fourth floor is available for focused work. Please keep calls out of"
    " that room and use the small booths along the corridor instead.",
    "Our team lunch is moving to the new restaurant by the station this month. They can cater for"
    " most diets, so let the organiser know of any requirements by Wednesday.",
    "The archive boxes in the basement are being sorted next week. If any of them belong to your"
    " team and are still needed, please label them before the sorting starts.",
    "The new coffee beans have had good reviews so far. The previous blend will stay available"
    " for a while, as a few people have said they prefer it.",
    "The mentoring scheme is open for the next round. It pairs people from different teams for a"
    " few months, and past mentees have found it useful for meeting colleagues.",
    "The water fountain near the lifts is being replaced with a filtered tap. While the work is"
    " done, please use the kitchens for drinking water.",
    "The lost property box at reception is getting full. Anything not claimed by the end of the"
    " month will be donated, so please check if you have mislaid anything.",
    "The training budget for the year has been set, and each team has its share. Talk to your"
    " manager about any courses or conferences you would like to attend.",
    "The window cleaners are working on the south side of the building on Monday. You may want"
    " to keep the blinds down that morning if you sit by the windows.",
    "The shared drive for the planning documents has been tidied. Older versions are in an"
    " archive folder, and the current ones are at the top level with clear names.",
    "The volunteering day this year is with the local park trust. We will be planting trees and"
    " clearing paths, and gloves and tools will be provided on the day.",
    "A reminder that the building closes at eight in the evening. If you need to work later,"
    " please let security know so they can plan their rounds.",
)

# a friend, neighbour or relative writing to a personal mailbox
PERSONAL = (
    "It was lovely to catch up at the weekend. The garden looked wonderful, and I have been"
    " meaning to ask what you planted along the fence; the colours were really striking.",
    "We finally finished painting the spare room. It took far longer than planned, mostly"
    " because we changed our minds about the colour twice, but it looks much brighter now.",
    "The dog has been enjoying the cooler mornings. We have started walking the long way round"
    " the lake, and she always stops to say hello to the ducks by the old boathouse.",
    "Thank you again for the recipe. I made the soup on Sunday and everyone went back for"
    " seconds, even my brother, who usually refuses anything with lentils in it.",
    "The school play was a great success. The children had clearly practised for weeks, and the"
    " costumes were remarkable considering most of them were made from old curtains.",
    "We are thinking of a short trip to the coast next month. Nothing fancy, just a couple of"
    " nights somewhere quiet with a good walk and a decent place for fish and chips.",
    "The new neighbours seem very nice. They came over with a plant when they moved in, and we"
    " have promised to have them round for dinner once they have settled.",
    "My knee is much better now, thank you for asking. The physio exercises are dull but they"
    " work, and I managed the whole hill on our walk last week without stopping.",
    "The book you lent me was wonderful. I stayed up far too late finishing it, and I keep"
    " thinking about the ending; we should talk about it next time we meet.",
    "The allotment has done well this year. We have more courgettes than we know what to do"
    " with, so do let me know if you would like some when we next see you.",
    "Mum is settling into the new flat. She likes being closer to the shops, and the lady across"
    " the hall has already invited her to the weekly coffee morning.",
    "We went to the farmers' market on Saturday and came home with far too much cheese. The"
    " bread stall was excellent too, and the man there remembered us from last time.",
    "The weather has finally turned. We lit the fire for the first time this season last night,"
    " and the cat has claimed the rug in front of it as her own.",
    "I have joined a walking group that meets on Sunday mornings. They are a friendly lot, and"
    " we always end up at the same cafe for tea and a slice of cake afterwards.",
    "The photos from the wedding came out beautifully. I will put a few in the post for you, as"
    " I know you prefer prints to looking at them on a screen.",
    "We had a power cut on Tuesday evening and ended up playing cards by candlelight. It was"
    " rather fun, though the children were less keen once they realised the internet was off.",
    "The tomatoes on the windowsill are finally ripening. It has taken all summer, but the first"
    " few were delicious, and there are plenty more still turning red.",
    "I bumped into your cousin at the station last week. She looked well and asked to be"
    " remembered to you; she is still working at the library and seems to enjoy it.",
    "The repairs to the roof are done at last. The builders were tidy and quick once they"
    " started, and we no longer need the bucket in the attic when it rains.",
    "We are looking forward to seeing you at the end of the month. The spare bed is made up, and"
    " I have found the board game we used to play, so be ready for a rematch.",
    "The village fete is next Saturday. There will be the usual cake stall and dog show, and I"
    " have somehow been talked into helping with the tombola.",
    "I took up watercolours again after many years. My first few attempts were muddy, but the"
    " evening class has helped, and I am quite pleased with a painting of the harbour.",
    "Dad has been busy in his workshop building a bird table. It is a little lopsided, but the"
    " robins do not seem to mind, and he watches them from the kitchen every morning.",
    "The children are back at school and the house is very quiet. I have been catching up on the"
    " jobs I kept putting off over the summer, starting with the cupboard under the stairs.",
    "We watched the meteor shower from the back garden last night. It was cold, but we saw"
    " several bright ones, and the children talked about it all through breakfast.",
    "The choir concert went well. We were nervous about the new piece, but it came together on"
    " the night, and the church was nearly full, which was lovely to see.",
    "Thank you for the birthday card. It arrived on the day, and the picture on the front made"
    " me laugh; it looks just like the cottage we stayed in all those years ago.",
    "I have started baking bread at the weekends. The first loaf was rather dense, but they are"
    " getting better, and the kitchen smells wonderful while it is in the oven.",
    "The kittens next door have discovered our garden. They chase leaves across the lawn every"
    " afternoon, and I confess I spend far too long watching them from the window.",
    "We drove up to the lakes for a day last week. The leaves were just starting to change, and"
    " we had a picnic by the water before the clouds came in.",
    "My sister is expecting her second child in the spring. We are all delighted, and her eldest"
    " has already announced that he would like a brother, please.",
    "The local library has started a repair cafe once a month. I took the old lamp along, and a"
    " retired electrician had it working again within twenty minutes.",
    "The swimming lessons are going well. Our youngest can now manage a length without the"
    " float, and she is very proud of the certificate she brought home.",
    "We have been clearing out the loft and found a box of old letters from Grandma. Some of"
    " them are very funny, and I will bring a few along for you to read.",
    "The fox has been visiting the garden again. We see it most evenings just after dark,"
    " trotting along the fence and stopping to look at the house before it goes.",
    "I finally got round to framing the map you gave us. It is hanging in the hall now, and"
    " visitors always stop to look for the places they know.",
    "Our walk on Sunday took us past the old mill. They are turning it into flats, but the water"
    " wheel is being kept, which seems a nice way to remember what it was.",
    "The apple tree has given us a huge crop this year. We have made crumble, chutney and juice,"
    " and there are still boxes of apples in the garage waiting for a plan.",
    "It was good to hear your news on the phone. I am glad the new job is going well, and that"
    " the journey is shorter than you expected once you found the right train.",
    "The neighbours' son has been practising the trumpet. He is improving, slowly, and I have"
    " started to recognise the tune, which I think is meant to be a carol.",
    "We spent a rainy afternoon at the museum in town. The new gallery about the old railway was"
    " excellent, and the children loved the model trains in the last room.",
    "The hedge finally got its trim on Saturday. It took most of the morning, and the garden"
    " waste bin is full, but the front of the house looks much tidier for it.",
    "Our holiday photos are finally sorted into albums. It brought back lovely memories, and we"
    " laughed at the one of you trying to put up the tent in the wind.",
    "The street party for the jubilee is being planned already. Someone suggested bunting all"
    " the way down the road, so I have offered to help sew it over the winter.",
)

VOICES = {"vendor": VENDOR, "internal": INTERNAL, "personal": PERSONAL}
