Simple script to search for duplicate images in a folder and remove them.

`cli.py` is simpler and doesn't offer a way to select and remove images, but it can dump a file with the duplicate files to remove.

`ui.py` has more features and can show the duplicate pictures and allow to select which one to keep (all the other duplicates will be marked for removal).

You can save and later reload your current "session", so that you can run the analysis just once, and interrupt/resume the selection of duplicates.
This can be useful when you have many duplicates.
