.PHONY: build install clean

DSENT_DIR := hwcomponents_dsent/dsent
OBJ_DIR := .obj
DSENT_SRCS := $(shell find $(DSENT_DIR) -name '*.cc')
DSENT_OBJS := $(patsubst $(DSENT_DIR)/%.cc,$(OBJ_DIR)/%.o,$(DSENT_SRCS))
CXX ?= g++
CXXFLAGS ?= -O2 -std=c++11 -w
CXXFLAGS += -I$(DSENT_DIR)

build: $(DSENT_DIR)/dsent
	test -x $(DSENT_DIR)/dsent

$(DSENT_DIR)/dsent: $(DSENT_OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^

$(OBJ_DIR)/%.o: $(DSENT_DIR)/%.cc
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) -c -o $@ $<

install:
	$(MAKE) build
	pip3 install .

clean:
	rm -rf $(OBJ_DIR) $(DSENT_DIR)/dsent
