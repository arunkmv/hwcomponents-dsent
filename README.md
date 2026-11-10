# HWComponents-DSENT

This package connects [DSENT](https://github.com/gem5/gem5/tree/stable/ext/dsent) to HWComponents. 
It provides models for electrical network-on-chip routers and links.

These models are for use with the HWComponents package, found at
https://accelergy-project.github.io/hwcomponents/.

## Installation

```bash
make build      # compile the standalone DSENT executable
pip install .

# Check that the installation is successful
hwc --list | grep -iE 'router|link'
```

## Models

- `Router`: input-buffered virtual-channel router. Actions: `route_flit` (buffer write +
  buffer read + switch arbitration + crossbar traversal), and the individual
  `write_buffer`, `read_buffer`, `arbitrate`, `traverse_crossbar`. Clock-tree power is
  activity-independent and is included in `leak_power`.
- `NoCLink`: repeated-wire link. Action: `send`.

```python
from hwcomponents_dsent import Router, NoCLink

router = Router(tech_node=22e-9, flit_width=64, n_in_ports=5, n_out_ports=5)
link = NoCLink(tech_node=22e-9, width=64, length=1e-3)
print(router.route_flit(), router.leak_power, router.area)
print(link.send(), link.leak_power, link.area)
```

DSENT ships 22, 32, and 45nm bulk technology models. Other nodes are linearly
interpolated or extrapolated. Pass `include_11nm_trigate=True` to also use DSENT's 11nm
tri-gate model.

## Citation

```bibtex
@inproceedings{sun2012dsent,
  author={Sun, Chen and Chen, Chia-Hsin Owen and Kurian, George and Wei, Lan and Miller, Jason and Agarwal, Anant and Peh, Li-Shiuan and Stojanovic, Vladimir},
  booktitle={2012 IEEE/ACM Sixth International Symposium on Networks-on-Chip (NOCS)},
  title={DSENT - A Tool Connecting Emerging Photonics with Electronics for Opto-Electronic Networks-on-Chip Modeling},
  year={2012},
  pages={201-210},
  doi={10.1109/NOCS.2012.31}
}
```
