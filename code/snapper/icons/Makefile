INKSCAPE=/usr/bin/inkscape
CONVERT=/usr/bin/convert
FLAGS=-z -w 28 -h 28

PNGS=cycle.png  dir.png  flip.png  pos.png  reset.png  right.png  rotate.png  snap_off.png  snap.png  up.png normal.png accept.png connect.png copy.png pie.png

%.png: %.svg
	$(INKSCAPE) $(FLAGS) -e $@ $<
	$(CONVERT) $@ -channel RGB -negate $@

all: $(PNGS)

.PHONY: clean

clean:
	rm -f $(ODIR)/*.o *~ core $(INCDIR)/*~ 
