// Standalone DSENT driver for hwcomponents-dsent.
//
// Usage: dsent <config file> <output file>
//
// Builds the model described by the config file, evaluates its EvaluateString,
// and writes every `print "<name>" <expr>;` result to the output file as one
// "<name>\t<value>" line. Values are written with full precision.

#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <string>

#include "DSENT.h"

using namespace std;

int main(int argc, char **argv)
{
    if (argc != 3) {
        cerr << "Usage: " << argv[0] << " <config file> <output file>" << endl;
        return 1;
    }

    map<LibUtil::String, LibUtil::String> config;
    DSENT::Model *model = DSENT::initialize(argv[1], config);

    map<string, double> outputs;
    DSENT::run(config, model, outputs);

    ofstream out(argv[2]);
    if (!out) {
        cerr << "Could not open output file " << argv[2] << endl;
        return 1;
    }
    out << setprecision(numeric_limits<double>::max_digits10);
    for (const auto &it : outputs) {
        out << it.first << "\t" << it.second << "\n";
    }
    out.close();

    DSENT::finalize(config, model);
    return 0;
}
