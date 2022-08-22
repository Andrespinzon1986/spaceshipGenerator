from abc import ABC
import base64
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zipfile import ZipFile
import uuid

import dash
import dash_bootstrap_components as dbc
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from dash import ALL, dcc, html
from dash.dependencies import Input, Output, State
from pcgsepy.common.api_call import block_definitions
from pcgsepy.common.jsonifier import json_dumps, json_loads
from pcgsepy.config import (BIN_POP_SIZE, CS_MAX_AGE, N_EMITTER_STEPS,
                            N_GENS_ALLOWED)
from pcgsepy.lsystem.rules import StochasticRules
from pcgsepy.lsystem.solution import CandidateSolution
from pcgsepy.mapelites.behaviors import (BehaviorCharacterization, avg_ma,
                                         mame, mami, symmetry)
from pcgsepy.mapelites.bin import MAPBin
from pcgsepy.mapelites.emitters import (ContextualBanditEmitter, GreedyEmitter,
                                        HumanEmitter, HumanPrefMatrixEmitter,
                                        PreferenceBanditEmitter, RandomEmitter)
from pcgsepy.mapelites.map import MAPElites, get_elite
from pcgsepy.xml_conversion import convert_structure_to_xml
from tqdm import trange


class DashLoggerHandler(logging.StreamHandler):
    def __init__(self):
        logging.StreamHandler.__init__(self)
        self.queue = []

    def emit(self, record):
        t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = self.format(record)
        self.queue.append(f'[{t}]\t{msg}')


logger = logging.getLogger('webapp')
logger.setLevel(logging.DEBUG)
dashLoggerHandler = DashLoggerHandler()
logger.addHandler(dashLoggerHandler)


class Metric:
    def __init__(self,
                 multiple_values: bool = False) -> None:
        self.current_generation: int = 0
        self.multiple_values = multiple_values
        self.history: Dict[int, List[Any]] = {
            self.current_generation: [] if multiple_values else 0
        }
    
    def add(self,
            value: Any):
        if self.multiple_values:
            self.history[self.current_generation].append(value)
        else:
            self.history[self.current_generation] += value
    
    def reset(self):
        if self.multiple_values:
            self.history[self.current_generation] = []
        else:
            self.history[self.current_generation] = 0
    
    def new_generation(self):
        self.current_generation += 1
        self.reset()
    
    def get_averages(self) -> List[Any]:
        return [np.mean(l) for l in self.history.values()]


n_spaceships_inspected = Metric()
time_elapsed = Metric(multiple_values=True)


hm_callback_props = {}
block_to_colour = {
    # colours from https://developer.mozilla.org/en-US/docs/Web/CSS/color_value
    'LargeBlockArmorCorner': '#778899',
    'LargeBlockArmorSlope': '#778899',
    'LargeBlockArmorCornerInv': '#778899',
    'LargeBlockArmorBlock': '#778899',
    'LargeBlockGyro': '#2f4f4f',
    'LargeBlockSmallGenerator': '#ffa07a',
    'LargeBlockSmallContainer': '#008b8b',
    'OpenCockpitLarge': '#32cd32',
    'LargeBlockSmallThrust': '#ff8c00',
    'SmallLight': '#fffaf0',
    'Window1x1Slope': '#fffff0',
    'Window1x1Flat': '#fffff0',
    'LargeBlockLight_1corner': '#fffaf0'
}
gdev_mode: bool = False
gen_counter: int = 0
selected_bins: List[Tuple[int, int]] = []
exp_n: int = 0
rngseed: int = 42
current_mapelites = None
step_progress = -1
consent_ok = None
# TODO: create these
my_emitterslist = ['mapelites_random.json',
                   'mapelites_prefmatrix.json',
                   'mapelites_prefbandit.json',
                   'mapelites_contbandit.json']


def resource_path(relative_path):
# get absolute path to resource
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


app = dash.Dash(__name__,
                title='Spaceships Generator',
                # external_stylesheets=[dbc.themes.CYBORG],
                external_stylesheets=[dbc.themes.DARKLY],
                # external_stylesheets=[dbc.themes.FLATLY],
                # external_stylesheets=[dbc.themes.LUMEN],
                # external_stylesheets=[dbc.themes.MORPH],
                assets_folder=resource_path("assets"),
                update_title=None)


def set_callback_props(mapelites: MAPElites):
    hm_callback_props['pop'] = {
        'Feasible': 'feasible',
        'Infeasible': 'infeasible'
    }
    hm_callback_props['metric'] = {
        'Fitness': {
            'name': 'fitness',
            'zmax': {
                'feasible': sum([x.weight * x.bounds[1] for x in mapelites.feasible_fitnesses]) + mapelites.nsc,
                'infeasible': 1.
            },
            'colorscale': 'Inferno'
        },
        'Age':  {
            'name': 'age',
            'zmax': {
                'feasible': CS_MAX_AGE,
                'infeasible': CS_MAX_AGE
            },
            'colorscale': 'Greys'
        },
        'Coverage': {
            'name': 'size',
            'zmax': {
                'feasible': BIN_POP_SIZE,
                'infeasible': BIN_POP_SIZE
            },
            'colorscale': 'Hot'
        }
    }
    hm_callback_props['method'] = {
        'Population': True,
        'Elite': False
    }


def get_properties_table(cs: Optional[CandidateSolution] = None) -> dbc.Table:
    size = str(cs.size) if cs else '-' 
    nblocks = cs.n_blocks if cs else '-'
    vol = cs.content.total_volume if cs else '-'
    mass = cs.content.mass if cs else '-'
    
    table_header = [
        html.Thead(html.Tr([html.Th("Property", style={'text-align': 'center'}),
                            html.Th("Value", style={'text-align': 'center'})]))
        ]
    table_body = [html.Tbody([
        html.Tr([html.Td("Spaceship size"), html.Td(size, style={'text-align': 'center'})]),
        html.Tr([html.Td("Number of blocks"), html.Td(nblocks, style={'text-align': 'center'})]),
        html.Tr([html.Td("Occupied volume"), html.Td(f'{vol} m³', style={'text-align': 'center'})]),
        html.Tr([html.Td("Spaceship mass"), html.Td(f'{mass} Kg', style={'text-align': 'center'})]),
    ])]
    
    return table_header + table_body
   
 
def set_app_layout(behavior_descriptors_names,
                   mapelites: Optional[MAPElites] = None,
                   dev_mode: bool = True):
    
    global current_mapelites
    global rngseed
    global consent_ok
    global gdev_mode
    
    gdev_mode = dev_mode
    
    webapp_info_file = './assets/help_dev.md' if dev_mode else './assets/webapp_info.md'
    with open(webapp_info_file, 'r') as f:
        webapp_info_str = f.read()
        
    algo_info_file = './assets/algo_info.md'
    with open(algo_info_file, 'r') as f:
        algo_info_str = f.read()
    
    quickstart_info_file = './assets/quickstart.md'
    with open(quickstart_info_file, 'r', encoding='utf-8') as f:
        quickstart_info_str = f.read()
    
    # rngseed = random.randint(0, 128)
    rngseed = uuid.uuid4().int
    if mapelites is None:
        random.seed(rngseed)
        random.shuffle(my_emitterslist)
        with open(my_emitterslist[0], 'r') as f:
            mapelites = json_loads(f.read())
    current_mapelites = mapelites
    
    logging.getLogger('webapp').info(msg=f'Your ID is {str(rngseed).zfill(3)}.')
    
    consent_dialog = dbc.Modal([
            dbc.ModalHeader(dbc.ModalTitle("Privacy policy"), close_button=False),
            dbc.ModalBody(children=[dcc.Markdown("""If you agree, we will share your usage statistics for scientific purposes with [Araya Inc.](https://www.araya.org/en/), who developed the Spaceship AI Generator with the help of GoodAI.
We would like to understand your level of engagement with the generator and ask you for feedback in a Google form questionnaire in order to further improve the application.
You can use the application without agreeing to the privacy policy; in such case, we will not be collecting your usage statistics and you will not be prompted for feedback."""),
                                    dcc.Markdown("Do you agree with the privacy policy?",
                                                 style={'text-align': 'center'})
                                    ]),
            dbc.ModalFooter(children=[
                dbc.Button("No",
                           id="consent-no",
                           color="danger",
                           className="ms-auto",
                           n_clicks=0,
                           style={'width': '49%'}),
                dbc.Button("Yes",
                           id="consent-yes",
                           color="success",
                           className="ms-auto",
                           n_clicks=0,
                           style={'width': '49%'})
                ])
            ],
        id="consent-modal",
        centered=True,
        backdrop="static",
        keyboard=False,
        is_open=consent_ok is None)
    
    webapp_info_modal = dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Webapp Info"), close_button=True),
        dbc.ModalBody(dcc.Markdown(webapp_info_str))
    ],
                           id='webapp-info-modal',
                           centered=True,
                           backdrop='static',
                           is_open=False,
                           scrollable=True,
                           size='lg')
    
    algo_info_modal = dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("AI Info"), close_button=True),
        dbc.ModalBody(dcc.Markdown(algo_info_str,
                                   mathjax=True))
    ],
                           id='algo-info-modal',
                           centered=True,
                           backdrop='static',
                           is_open=False,
                           scrollable=True,
                           size='lg')
    
    quickstart_modal = dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Quickstart"), close_button=True),
        dbc.ModalBody(dcc.Markdown(quickstart_info_str,
                                   link_target="_blank"))
    ],
                           id='quickstart-modal',
                           centered=True,
                           backdrop='static',
                           is_open=False,
                           scrollable=True,
                           size='lg')

    header = dbc.Row(children=[
                dbc.Col(html.H1(children='🚀Space Engineers Spaceships Generator🚀',
                                className='title'), width={'size': 8, 'offset': 2}),
                dbc.Col(children=[dbc.Button('Webapp Info',
                                             id='webapp-info-btn',
                                             color='info')],
                        align='center', width=1),
                dbc.Col(children=[dbc.Button('AI Info',
                                             id='ai-info-btn',
                                             color='info')],
                        align='center', width=1)
    ],
                     className='header')
    
    no_bins_selected_modal = dbc.Modal(children=[
        dbc.ModalHeader(dbc.ModalTitle("Error"), close_button=True),
        dbc.ModalBody('You must choose a spaceship from the grid on the left before evolving it!'),
        dbc.ModalFooter(children=[dbc.Button("Understood",
                                             id="nbs-err-btn",
                                             color="primary",
                                             className="ms-auto",
                                             n_clicks=0)]),
        ],
                                       id='nbs-err-modal',
                                       centered=True,
                                       backdrop='static',
                                       is_open=False)
    
    exp_progress = html.Div(
        children=[
            dbc.Label(f'Current iteration:'),
            dbc.Progress(id="gen-progress",
                         color='success',
                         striped=False,
                         animated=False),
            html.Br(),
            dbc.Label(f'Spaceships generation progress:'),
            dbc.Progress(id="exp-progress",
                         color='success',
                         striped=False,
                         animated=False),
        ],
        id='exp-progress-div')
    
    mapelites_heatmap = html.Div(children=[
        dcc.Graph(id="heatmap-plot",
                  figure=go.Figure(data=[]),
                  config={
                      'displayModeBar': False,
                      'displaylogo': False, 
                      'scrollZoom': True})
        ])
    
    mapelites_controls = html.Div(
        children=[
            html.H6(children='Plot settings',
                    className='section-title'),
            dbc.Label('Choose which population to display.'),
            dbc.DropdownMenu(label='Feasible',
                            children=[
                                dbc.DropdownMenuItem('Feasible', id='population-feasible'),
                                dbc.DropdownMenuItem('Infeasible', id='population-infeasible'),
                            ],
                            id='population-dropdown'),
            html.Br(),
            dbc.Label('Choose which metric to plot.'),
            dbc.DropdownMenu(label='Fitness',
                            children=[
                                dbc.DropdownMenuItem('Fitness', id='metric-fitness'),
                                dbc.DropdownMenuItem('Age', id='metric-age'),
                                dbc.DropdownMenuItem('Coverage', id='metric-coverage'),
                            ],
                            id='metric-dropdown'),
            html.Br(),
            dbc.Label('Choose whether to compute the metric for the entire bin population or just the elite.'),
            dbc.RadioItems(id='method-radio',
                        options=[
                            {'label': 'Population', 'value': 'Population'},
                            {'label': 'Elite', 'value': 'Elite'}
                        ],
                        value='Population')
            ])
    
    content_plot = html.Div(children=[
        dcc.Graph(id="content-plot",
                  figure=go.Figure(data=[]),
                  config={
                      'displayModeBar': False,
                      'displaylogo': False}),
        ])
    
    content_properties = html.Div(
        children=[
            html.H6('Spaceship properties',
                    className='section-title'),
            dbc.Table(children=get_properties_table(),
                      id='spaceship-properties',
                      bordered=True,
                      dark=True,
                      hover=True,
                      responsive=True,
                      striped=True),
            html.Div(children=[
                dbc.Button('Download content',
                            id='download-btn',
                            disabled=True),
                dcc.Download(id='download-content'),
                dcc.Download(id='download-metrics')
                ])
            ])
    
    if dev_mode:
        content_properties.children.insert(len(content_properties.children) - 2, html.P(children='Content string: '))
        content_properties.children.insert(len(content_properties.children) - 2,  dbc.Textarea(id='content-string',
                                                                                               value='',
                                                                                               contentEditable=False,
                                                                                               disabled=True,
                                                                                               class_name='content-string-area'))
    
    experiment_settings = html.Div(
        children=[
            html.H6(children='Experiment settings',
                    className='section-title'),
            html.Br(),
            html.Div(children=[
                html.P(children='Valid bins are: ',
                       id='valid-bins'),
                html.P(children=f'Current generation: {gen_counter}',
                       id='gen-display'),
                html.P(children=f'Selected bin(s): {selected_bins}',
                       id='selected-bin')
                ]),
            html.Br(),
            dbc.InputGroup(children=[
                dbc.InputGroupText('Feature descriptors (X, Y):'),
                dbc.DropdownMenu(label=current_mapelites.b_descs[0].name,
                             children=[
                                 dbc.DropdownMenuItem('Major axis / Medium axis', id='bc0-Major-axis_Medium-axis'),
                                 dbc.DropdownMenuItem('Major axis / Smallest axis', id='bc0-Major-axis_Smallest-axis'),
                                 dbc.DropdownMenuItem('Average Proportions', id='bc0-Average-Proportions'),
                                 dbc.DropdownMenuItem('Symmetry', id='bc0-Symmetry')
                                 ],
                             id='b0-dropdown'),
                dbc.DropdownMenu(label=current_mapelites.b_descs[1].name,
                             children=[
                                 dbc.DropdownMenuItem('Major axis / Medium axis', id='bc1-Major-axis_Medium-axis'),
                                 dbc.DropdownMenuItem('Major axis / Smallest axis', id='bc1-Major-axis_Smallest-axis'),
                                 dbc.DropdownMenuItem('Average Proportions', id='bc1-Average-Proportions'),
                                 dbc.DropdownMenuItem('Symmetry', id='bc1-Symmetry')
                                 ],
                             id='b1-dropdown')
                ],
                           className="mb-3",
                           style={'content-visibility': 'hidden' if not dev_mode else 'visible'}),
            dbc.InputGroup(children=[
                dbc.InputGroupText('Toggle L-system modules:'),
                dbc.Checklist(id='lsystem-modules',
                              options=[{'label': x.name, 'value': x.name} for x in current_mapelites.lsystem.modules],
                              value=[x.name for x in current_mapelites.lsystem.modules if x.active],
                              inline=True,
                              switch=True)
                ],
                           style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                           className="mb-3"),
            dbc.InputGroup(children=[
                dbc.InputGroupText('Fitness weights:'),
                html.Div(children=[
                    html.Div(children=[
                        dbc.Label(children=f.name),
                        html.Div(children=[
                            dcc.Slider(min=0,
                                       max=1,
                                       step=0.1,
                                       value=1,
                                       marks=None,
                                       tooltip={"placement": "bottom",
                                                "always_visible": True},
                                       id={'type': 'fitness-sldr',
                                           'index': i})
                        ],
                                 )
                        ]) for i, f in enumerate(current_mapelites.feasible_fitnesses)
                    ])
                ],
                           style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                           className="mb-3"),
            dbc.InputGroup(children=[
                dbc.InputGroupText('Select emitter:'),
                dbc.DropdownMenu(label='Human',
                             children=[
                                 dbc.DropdownMenuItem('Human'),
                                 dbc.DropdownMenuItem('Random'),
                                 dbc.DropdownMenuItem('Greedy'),
                                 dbc.DropdownMenuItem('Preference Matrix'),
                                 dbc.DropdownMenuItem('Preference Bandit'),
                                 dbc.DropdownMenuItem('Contextual Bandit'),
                             ],
                             id='emitter-dropdown')
                ],
                           className="mb-3",
                           style={'content-visibility': 'hidden' if not dev_mode else 'visible'}),
            dbc.InputGroup(children=[
                dbc.InputGroupText('Enforce symmetry:'),
                dbc.DropdownMenu(label='None',
                             children=[
                                 dbc.DropdownMenuItem('None', id='symmetry-none'),
                                 dbc.DropdownMenuItem('X-axis', id='symmetry-x'),
                                 dbc.DropdownMenuItem('Y-axis', id='symmetry-y'),
                                 dbc.DropdownMenuItem('Z-axis', id='symmetry-z'),
                             ],
                             id='symmetry-dropdown'),
                dbc.RadioItems(id='symmetry-radio',
                        options=[
                            {'label': 'Upper', 'value': 'Upper'},
                            {'label': 'Lower', 'value': 'Lower'}
                        ],
                        value='Upper')
                ],
                           style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                           className="mb-3"),
            dbc.InputGroup(children=[
                dbc.InputGroupText('Save/load population:'),
                dbc.Button(id='popdownload-btn',
                           children='Download current population'),
                dcc.Upload(
                    id='popupload-data',
                    children='Upload population',
                    multiple=False
                    ),
                ],
                           className="mb-3",
                           style={'content-visibility': 'hidden' if not dev_mode else 'visible'})
            ],
        style={'content-visibility': 'hidden' if not dev_mode else 'visible'})
    
    experiment_controls = html.Div(
        children=[
            html.H6('Experiment controls',
                    className='section-title'),
            html.Br(),
            dbc.Row(dbc.Col(children=[
                dbc.Button(id='step-btn',
                           children='Evolve from spaceship',
                           className='button-fullsize')
                ],
                    className='spacer',
                    width={'size': 4, 'offset':4})),
            dbc.Row(dbc.Col(children=[
                dbc.Button(id='selection-clr-btn',
                       children='Clear selection',
                           className='button-fullsize')
                ],
                    className='spacer',
                    style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                    width={'size': 4, 'offset':4})),
            dbc.Row(dbc.Col(children=[
                dbc.Button(id='selection-btn',
                       children='Toggle single bin selection',
                           className='button-fullsize')
                ],
                    className='spacer',
                    style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                    width={'size': 4, 'offset':4})),
            dbc.Row(dbc.Col(children=[
                dbc.Button(id='reset-btn',
                           children='Initialize/Reset',
                           className='button-fullsize')
                ],
                    className='spacer',
                    style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                    width={'size': 4, 'offset':4})),
            dbc.Row(dbc.Col(children=[
                dbc.Button(id='subdivide-btn',
                       children='Subdivide selected bin(s)',
                           className='button-fullsize')
                ],
                    className='spacer',
                    style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                    width={'size': 4, 'offset':4})),
            dbc.Row(dbc.Col(children=[
                dbc.Button(id='download-mapelites-btn',
                           children='Download MAP-Elites',
                           className='button-fullsize'),
                dcc.Download(id='download-mapelites')
                ],
                    className='spacer',
                    style={'content-visibility': 'hidden' if not dev_mode else 'visible'},
                    width={'size': 4, 'offset':4})),
        ])
    
    rules = html.Div(
        children=[
            html.H6(children='High-level rules',
                    className='section-title'),
            dbc.Textarea(id='hl-rules',
                         value=str(current_mapelites.lsystem.hl_solver.parser.rules),
                         wrap=False,
                         className='rules-area'),
            dbc.Button(children='Update high-level rules',
                       id='update-rules-btn')
            ],
        style={'content-visibility': 'hidden' if not dev_mode else 'visible'})
    
    progress = html.Div(
        children=[
            html.Br(),
            dbc.Label('Evolution progress: '),
            dbc.Progress(id="step-progress",
                         color='info',
                         striped=True,
                         animated=True)
        ],
        id='step-progress-div',
        style={'content-visibility': 'visible' if 0 <= step_progress <= 100 else 'hidden'})
    
    log = html.Div(
        children=[
            dcc.Interval(id='interval1',
                         interval=1 * 1000,
                         n_intervals=0),
            html.H6(children='Log',
                    className='section-title'),
            dbc.Textarea(id='console-out',
                         value='',
                         wrap=False,
                         contentEditable=False,
                         disabled=True,
                         className='log-area')
            ])
    
    if dev_mode:
        app.layout = dbc.Container(
            children=[
                consent_dialog,
                header,
                webapp_info_modal,
                algo_info_modal,
                quickstart_modal,
                no_bins_selected_modal,
                html.Br(),
                dbc.Row(children=[
                    dbc.Col(mapelites_heatmap, width=3),
                    dbc.Col(content_plot, width=7),
                    dbc.Col(content_properties, width=2)],
                        align="center"),
                dbc.Row(children=[
                    dbc.Col(mapelites_controls, width=3),
                    dbc.Col(experiment_settings, width=4),
                    dbc.Col(experiment_controls, width=3),
                    dbc.Col(rules, width=2)],
                        align="start"),
                dbc.Row(children=[
                    dbc.Col(progress, width={'size': 4, 'offset': 4},
                            className='spacer')]),
                dbc.Row(children=[
                    dbc.Col(log, width={'size': 4, 'offset': 4})],
                        align="end"),
                
                html.Div(id='hidden-div',
                         children=[],
                         style={'visibility': 'hidden', 'height': '0px'})
                ],
            fluid=True)
    else:
        app.layout = dbc.Container(
            children=[
                consent_dialog,
                header,
                webapp_info_modal,
                algo_info_modal,
                quickstart_modal,
                no_bins_selected_modal,
                html.Br(),
                dbc.Row(children=[
                    dbc.Col(mapelites_heatmap, width=3),
                    dbc.Col(content_plot, width=7),
                    dbc.Col(content_properties, width=2)],
                        align="center"),
                dbc.Row(children=[
                    dbc.Col(children=[exp_progress,
                                      progress,
                                      experiment_settings],
                            width=4),
                    dbc.Col(experiment_controls, width=4),
                    dbc.Col(log, width=4)],
                        align="start"),
                
                # add other components but hide them so Dash doesn't throw errors
                html.Div(id='hidden-div',
                         children=[
                             mapelites_controls,
                             rules,
                             dbc.Textarea(id='content-string',
                                          value='')
                             ],
                         style={'visibility': 'hidden', 'height': '0px'})
                ],
            fluid=True)


app.clientside_callback(
    """
    function(clicks) {
        if (clicks) {
            window.open("https://forms.gle/gsuajDXUNocZvDzn9", "_blank");
            return 0;
        }
    }
    """,
    Output("hidden-div", "n_clicks"),  # super hacky but Dash leaves me no choice
    Input("consent-yes", "n_clicks")
)


@app.callback(
    Output("webapp-info-modal", "is_open"),
    Input("webapp-info-btn", "n_clicks"),
    prevent_initial_call=True
)
def show_webapp_info(n):
    return True


@app.callback(
    Output("algo-info-modal", "is_open"),
    Input("ai-info-btn", "n_clicks"),
    prevent_initial_call=True
)
def show_algo_info(n):
    return True


@app.callback(Output('console-out', 'value'),
              Input('interval1', 'n_intervals'))
def update_output(n):
    return ('\n'.join(dashLoggerHandler.queue))


@app.callback(
    [Output("step-progress", "value"),
     Output("step-progress", "label"),
     Output('step-progress-div', 'style')],
    [Input("interval1", "n_intervals")],
)
def update_progress(n):  
    return step_progress, f"{step_progress}%", {'content-visibility': 'visible' if 0 <= step_progress <= 100 else 'hidden'}


@app.callback(
    [Output("gen-progress", "value"),
     Output("gen-progress", "label")],
    [Input("interval1", "n_intervals")],
)
def update_gen_progress(n):
    val = 100 * ((gen_counter) / N_GENS_ALLOWED)
    return val, f"{gen_counter} / {N_GENS_ALLOWED}"


@app.callback(
    [Output("exp-progress", "value"),
     Output("exp-progress", "label")],
    [Input("interval1", "n_intervals")],
)
def update_exp_progress(n):
    val = 100 * ((1 + exp_n) / len(my_emitterslist))
    return val, f"{exp_n + 1} / {len(my_emitterslist)}"


def _from_bc_to_idx(bcs: Tuple[float, float],
                    mapelites: MAPElites) -> Tuple[int, int]:
    b0, b1 = bcs
    i = np.digitize([b0],
                    np.cumsum([0] + mapelites.bin_sizes[0]
                              [:-1]) + mapelites.b_descs[0].bounds[0],
                    right=False)[0] - 1
    j = np.digitize([b1],
                    np.cumsum([0] + mapelites.bin_sizes[1]
                              [:-1]) + mapelites.b_descs[1].bounds[0],
                    right=False)[0] - 1
    return (i, j)


@app.callback(
    Output("download-mapelites", "data"),
    Input("download-mapelites-btn", "n_clicks"),
    prevent_initial_call=True,
)
def download_mapelites(n_clicks):
    global current_mapelites
    global gen_counter
    
    t = datetime.now().strftime("%Y%m%d%H%M%S")
    fname = f'{t}_mapelites_{current_mapelites.emitter.name}_gen{str(gen_counter).zfill(2)}'
    return dict(content=json_dumps(current_mapelites), filename=f'{fname}.json')


def _switch(ls: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    res = []
    for e in ls:
        res.append((e[1], e[0]))
    return res


def _get_valid_bins(mapelites: MAPElites):
    valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
    return _switch(valid_bins)


def format_bins(mapelites: MAPElites,
                bins_idx_list: List[Tuple[int, int]],
                str_prefix: str,
                do_switch: bool = True,
                filter_out_empty: bool = True) -> Tuple[List[Tuple[int, int]], str]:
    bins_list: List[MAPBin] = [mapelites.bins[j, i] if do_switch else mapelites.bins[i, j] for (i, j) in bins_idx_list]
    sel_bins_str = f'{str_prefix}'
    for b in bins_list:
        if filter_out_empty:
            if b.non_empty(pop='feasible') or b.non_empty(pop='infeasible'):
                i, j = b.bin_idx
                i, j = (j, i) if do_switch else (i, j)
                bc1 = np.sum([mbin.bin_size[0] for mbin in mapelites.bins[:i, j]])
                bc2 = np.sum([mbin.bin_size[1] for mbin in mapelites.bins[i, :j]])
                sel_bins_str += f' {(i, j)} [{bc1}:{bc2}];'
            elif b.bin_idx in bins_idx_list:
                bins_idx_list.remove((i, j))
        else:
            i, j = b.bin_idx
            bc1 = np.sum([mbin.bin_size[0] for mbin in mapelites.bins[:i, j]])
            bc2 = np.sum([mbin.bin_size[1] for mbin in mapelites.bins[i, :j]])
            sel_bins_str += f' {(i, j)} [{bc1}:{bc2}];'
    return bins_idx_list, sel_bins_str


def _build_heatmap(mapelites: MAPElites,
                   pop_name: str,
                   metric_name: str,
                   method_name: str) -> go.Figure:
    valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
    metric = hm_callback_props['metric'][metric_name]
    use_mean = hm_callback_props['method'][method_name]
    population = hm_callback_props['pop'][pop_name]
    # build heatmap
    disp_map = np.zeros(shape=mapelites.bins.shape)
    text = []
    for i in range(mapelites.bins.shape[0]):
        for j in range(mapelites.bins.shape[1]):
            v = mapelites.bins[i, j].get_metric(metric=metric['name'],
                                                use_mean=use_mean,
                                                population=population)
            disp_map[i, j] = v
            s = '▣' if (i, j) in valid_bins else ''
            if j == 0:
                text.append([s])
            else:
                text[-1].append(s)
    # plot
    x_labels = np.cumsum([0] + mapelites.bin_sizes[0][:-1]) + mapelites.b_descs[0].bounds[0]
    y_labels = np.cumsum([0] + mapelites.bin_sizes[1][:-1]) + mapelites.b_descs[1].bounds[0]
    # title = f'{pop_name} population {metric_name.lower()} ({"Average" if use_mean else "Elite"})'
    title = 'Spaceships population'
    heatmap = go.Figure(
        data=go.Heatmap(
            z=disp_map,
            zmin=0,
            zmax=hm_callback_props['metric'][metric_name]['zmax'][population],
            x=x_labels,
            y=y_labels,
            hoverongaps=False,
            colorscale=hm_callback_props['metric'][metric_name]['colorscale'],
            text=text,
            texttemplate="%{text}",
            textfont={"color": 'rgba(238, 238, 238, 1.)'},
            colorbar={"title": {"text": "Fitness", "side": "right"}, 'orientation': 'v'}
            ))
    heatmap.update_xaxes(title=dict(text=mapelites.b_descs[0].name))
    heatmap.update_yaxes(title=dict(text=mapelites.b_descs[1].name))
    heatmap.update_coloraxes(colorbar_title_text=metric_name)
    heatmap.update_layout(title=dict(text=title),
                          autosize=False,
                          dragmode='pan',
                          clickmode='event+select',
                          paper_bgcolor='rgba(0,0,0,0)',
                          plot_bgcolor='rgba(0,0,0,0)',
                          template='plotly_dark')
    hovertemplate = f'{mapelites.b_descs[0].name}: X<br>{mapelites.b_descs[1].name}: Y<br>{metric_name}: Z<extra></extra>'
    hovertemplate = hovertemplate.replace('X', '%{x}').replace('Y', '%{y}').replace('Z', '%{z}')
    heatmap.update_traces(hovertemplate=hovertemplate,
                          selector=dict(type='heatmap'))
    heatmap.update_layout(
        xaxis={
            'tickvals': x_labels
        },
        yaxis={
            'tickvals': y_labels
        },
    )
    
    return heatmap


def _get_colour_mapping(block_types: List[str]) -> Dict[str, str]:
    colour_map = {}
    for block_type in block_types:
        c = block_to_colour.get(block_type, '#ff0000')
        if block_type not in colour_map.keys():
            colour_map[block_type] = c
    return colour_map


def _get_elite_content(mapelites: MAPElites,
                       bin_idx: Tuple[int, int],
                       pop: List[CandidateSolution]) -> go.Scatter3d:
    if bin_idx is not None:
        # get elite content
        elite = get_elite(mapelites=mapelites,
                          bin_idx=bin_idx,
                          pop=pop)
        
        structure = elite.content
        content = structure.as_grid_array
        arr = np.nonzero(content)
        x, y, z = arr
        cs = [content[i, j, k] for i, j, k in zip(x, y, z)]
        ss = [structure._clean_label(list(block_definitions.keys())[v - 1]) for v in cs]
        fig = px.scatter_3d(x=x,
                            y=y,
                            z=z,
                            color=ss,
                            color_discrete_map=_get_colour_mapping(ss),
                            labels={
                                'x': '',
                                'y': 'm',
                                'z': '',
                                'color': 'Block type'
                            },
                            title='Selected spaceship',
                            template='plotly_dark')
        
        ux, uy, uz = np.unique(x), np.unique(y), np.unique(z)
        ptg = .2
        show_x = [v for i, v in enumerate(ux) if i % (1 / ptg) == 0]
        show_y = [v for i, v in enumerate(uy) if i % (1 / ptg) == 0]
        show_z = [v for i, v in enumerate(uz) if i % (1 / ptg) == 0]
        
        fig.update_layout(
            scene=dict(
                xaxis={
                    'tickmode': 'array',
                    'tickvals': show_x,
                    'ticktext': [structure.grid_size * i for i in show_x],
                },
                yaxis={
                    'tickmode': 'array',
                    'tickvals': show_y,
                    'ticktext': [structure.grid_size * i for i in show_y],
                },
                zaxis={
                    'tickmode': 'array',
                    'tickvals': show_z,
                    'ticktext': [structure.grid_size * i for i in show_z],
                }
            )
        )
        
    else:
        fig = px.scatter_3d(x=np.zeros(0, dtype=object),
                            y=np.zeros(0, dtype=object),
                            z=np.zeros(0, dtype=object),
                            title='Selected spaceship',
                            template='plotly_dark')
    
    fig.update_traces(marker=dict(size=4,
                              line=dict(width=3,
                                        color='DarkSlateGrey')),
                      selector=dict(mode='markers'))
        
    camera = dict(
        up=dict(x=0, y=0, z=1),
        center=dict(x=0, y=0, z=0),
        eye=dict(x=2, y=2, z=2)
        )
    
    fig.update_layout(scene=dict(aspectmode='data'),
                      scene_camera=camera,
                      paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(0,0,0,0)')
    return fig


def _apply_step(mapelites: MAPElites,
                selected_bins: List[Tuple[int, int]],
                gen_counter: int) -> bool:
    global step_progress
    perc_step = 100 / (1 + N_EMITTER_STEPS)
    
    valid = True
    if mapelites.enforce_qnt:
        valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
        for bin_idx in selected_bins:
            valid &= bin_idx in valid_bins
    if valid:
        logging.getLogger('webapp').info(msg=f'Started step {gen_counter + 1}...')
        step_progress = 0
        mapelites.interactive_step(bin_idxs=selected_bins,
                                    gen=gen_counter)
        step_progress += perc_step
        logging.getLogger('webapp').info(msg=f'Completed step {gen_counter + 1} (created {mapelites.n_new_solutions} solutions); running {N_EMITTER_STEPS} additional emitter steps if available...')
        mapelites.n_new_solutions = 0
        with trange(N_EMITTER_STEPS, desc='Emitter steps: ') as iterations:
            for _ in iterations:
                mapelites.emitter_step(gen=gen_counter)
                step_progress += perc_step
        logging.getLogger('webapp').info(msg=f'Emitter step(s) completed (created {mapelites.n_new_solutions} solutions).')
        mapelites.n_new_solutions = 0
        step_progress = -1
        return True
    else:
        logging.getLogger('webapp').info(msg='Step not applied: invalid bin(s) selected.')
        return False


def _apply_reset(mapelites: MAPElites) -> bool:
    logging.getLogger('webapp').info(msg='Started resetting all bins (this may take a while)...')
    mapelites.reset()
    logging.getLogger('webapp').info(msg='Reset completed.')
    return True


behavior_descriptors = [
    BehaviorCharacterization(name='Major axis / Medium axis',
                             func=mame,
                             bounds=(0, 10)),
    BehaviorCharacterization(name='Major axis / Smallest axis',
                             func=mami,
                             bounds=(0, 20)),
    BehaviorCharacterization(name='Average Proportions',
                             func=avg_ma,
                             bounds=(0, 20)),
    BehaviorCharacterization(name='Symmetry',
                             func=symmetry,
                             bounds=(0, 1))
]


def _apply_bc_change(bcs: Tuple[str, str],
                     mapelites: MAPElites) -> bool:
    b0, b1 = bcs
    logging.getLogger('webapp').info(msg=f'Updating feature descriptors to ({b0}, {b1})...')
    b0 = behavior_descriptors[[b.name for b in behavior_descriptors].index(b0)]
    b1 = behavior_descriptors[[b.name for b in behavior_descriptors].index(b1)]
    mapelites.update_behavior_descriptors((b0, b1))
    logging.getLogger('webapp').info(msg='Feature descriptors update completed.')
    return True


def _apply_bin_subdivision(mapelites: MAPElites,
                           selected_bins: List[Tuple[int, int]]) -> bool:
    bin_idxs = [(x[1], x[0]) for x in selected_bins]
    for bin_idx in bin_idxs:
        mapelites.subdivide_range(bin_idx=bin_idx)
    logging.getLogger('webapp').info(msg=f'Subdivided bin(s): {selected_bins}.')
    return True


def _apply_modules_update(mapelites: MAPElites,
                          modules: List[str]) -> bool:
    all_modules = [x for x in mapelites.lsystem.modules]
    names = [x.name for x in all_modules]
    for i, module in enumerate(names):
        if module in modules and not all_modules[i].active:
            # activate module
            mapelites.toggle_module_mutability(module=module)
            logging.getLogger('webapp').info(msg=f'Enabled {module}.')
            break
        elif module not in modules and all_modules[i].active:
            # deactivate module
            mapelites.toggle_module_mutability(module=module)
            logging.getLogger('webapp').info(msg=f'Disabled {module}.')
            break
    return True


def _apply_rules_update(mapelites: MAPElites,
                        rules: str) -> bool:
    new_rules = StochasticRules()
    for rule in rules.split('\n'):
        lhs, p, rhs = rule.strip().split(' ')
        new_rules.add_rule(lhs=lhs,
                           rhs=rhs,
                           p=float(p))
    try:
        new_rules.validate()
        mapelites.lsystem.hl_solver.parser.rules = new_rules
        logging.getLogger('webapp').info(msg=f'L-system rules updated.')
        return True
    except AssertionError as e:
        logging.getLogger('webapp').info(msg=f'Failed updating L-system rules ({e}).')
        return False


def _apply_fitness_reweight(mapelites: MAPElites,
                            weights: List[float]) -> bool:
    mapelites.update_fitness_weights(weights=weights)
    logging.getLogger('webapp').info(msg='Updated fitness functions weights.')
    hm_callback_props['metric']['Fitness']['zmax']['feasible'] = sum([x.weight * x.bounds[1] for x in mapelites.feasible_fitnesses]) + mapelites.nsc
    return True


def _apply_bin_selection_toggle(mapelites: MAPElites) -> bool:
    mapelites.enforce_qnt = not mapelites.enforce_qnt
    logging.getLogger('webapp').info(msg=f'MAP-Elites single bin selection set to {mapelites.enforce_qnt}.')
    
    
def _apply_emitter_change(mapelites: MAPElites,
                          emitter_name: str) -> bool:
    if emitter_name == 'Random':
        mapelites.emitter = RandomEmitter()
        logging.getLogger('webapp').info(msg=f'Emitter set to {emitter_name}')
        return True
    if emitter_name == 'Greedy':
        mapelites.emitter = GreedyEmitter()
        logging.getLogger('webapp').info(msg=f'Emitter set to {emitter_name}')
        return True
    elif emitter_name == 'Preference-matrix':
        mapelites.emitter = HumanPrefMatrixEmitter()
        mapelites.emitter._build_pref_matrix(bins=mapelites.bins)
        logging.getLogger('webapp').info(msg=f'Emitter set to {emitter_name}')
        return True
    elif emitter_name == 'Contextual Bandit':
        mapelites.emitter = ContextualBanditEmitter()
        logging.getLogger('webapp').info(msg=f'Emitter set to {emitter_name}')
        return True
    elif emitter_name == 'Preference Bandit':
        mapelites.emitter = PreferenceBanditEmitter()
        logging.getLogger('webapp').info(msg=f'Emitter set to {emitter_name}')
        return True
    elif emitter_name == 'None':
        mapelites.emitter = HumanEmitter()
        logging.getLogger('webapp').info(msg=f'Emitter set to {emitter_name}')
        return True
    else:
        logging.getLogger('webapp').info(msg=f'Unrecognized emitter type {emitter_name}')
        return False


@app.callback(Output('heatmap-plot', 'figure'),
              Output('content-plot', 'figure'),
              Output('valid-bins', 'children'),
              Output('gen-display', 'children'),
              Output('hl-rules', 'value'),
              Output('selected-bin', 'children'),
              Output('content-string', 'value'),
              Output('spaceship-properties', 'children'),
              Output('download-btn', 'disabled'),
              Output('step-btn', 'disabled'),
              Output("download-content", "data"),
              Output("download-metrics", "data"),
              Output('population-dropdown', 'label'),
              Output('metric-dropdown', 'label'),
              Output('b0-dropdown', 'label'),
              Output('b1-dropdown', 'label'),
              Output('symmetry-dropdown', 'label'),
              Output("consent-modal", "is_open"),
              Output("quickstart-modal", "is_open"),
              Output("nbs-err-modal", "is_open"),
              
              State('heatmap-plot', 'figure'),
              State('hl-rules', 'value'),
              State('content-plot', 'figure'),
              State('content-string', 'value'),
              State('spaceship-properties', 'children'),
              State('population-dropdown', 'label'),
              State('metric-dropdown', 'label'),
              State('b0-dropdown', 'label'),
              State('b1-dropdown', 'label'),
              State('symmetry-dropdown', 'label'),
              State("consent-modal", "is_open"),
              State("quickstart-modal", "is_open"),
              
              Input('population-feasible', 'n_clicks'),
              Input('population-infeasible', 'n_clicks'),
              Input('metric-fitness', 'n_clicks'),
              Input('metric-age', 'n_clicks'),
              Input('metric-coverage', 'n_clicks'),
              Input('method-radio', 'value'),
              Input('step-btn', 'n_clicks'),
              Input('reset-btn', 'n_clicks'),
              Input('subdivide-btn', 'n_clicks'),
              Input({'type': 'fitness-sldr', 'index': ALL}, 'value'),
              
              Input('bc0-Major-axis_Medium-axis', 'n_clicks'),
              Input('bc0-Major-axis_Smallest-axis', 'n_clicks'),
              Input('bc0-Average-Proportions', 'n_clicks'),
              Input('bc0-Symmetry', 'n_clicks'),
              Input('bc1-Major-axis_Medium-axis', 'n_clicks'),
              Input('bc1-Major-axis_Smallest-axis', 'n_clicks'),
              Input('bc1-Average-Proportions', 'n_clicks'),
              Input('bc1-Symmetry', 'n_clicks'),
              Input('lsystem-modules', 'value'),
              Input('update-rules-btn', 'n_clicks'),
              Input('heatmap-plot', 'clickData'),
              Input('selection-btn', 'n_clicks'),
              Input('selection-clr-btn', 'n_clicks'),
              
              Input('emitter-dropdown', 'label'),
              Input("download-btn", "n_clicks"),
              Input('popdownload-btn', 'n_clicks'),
              Input('popupload-data', 'contents'),
              Input('symmetry-none', 'n_clicks'),
              Input('symmetry-x', 'n_clicks'),
              Input('symmetry-y', 'n_clicks'),
              Input('symmetry-z', 'n_clicks'),
              Input('symmetry-radio', 'value'),
              Input("consent-yes", "n_clicks"),
              Input("consent-no", "n_clicks"),
              Input("nbs-err-btn", "n_clicks")
              )
def general_callback(curr_heatmap, rules, curr_content, cs_string, cs_properties, pop_name, metric_name, b0, b1, symm_axis, privacy_modal_show, qs_modal_show,
                     pop_feas, pop_infeas, metric_fitness, metric_age, metric_coverage, method_name, n_clicks_step, n_clicks_reset, n_clicks_sub, weights,
                     b0_mame, b0_mami, b0_avgp, b0_sym, b1_mame, b1_mami, b1_avgp, b1_sym, modules, n_clicks_rules, clickData, selection_btn, clear_btn,
                     emitter_name, n_clicks_cs_download, n_clicks_popdownload, upload_contents, symm_none, symm_x, symm_y, symm_z, symm_orientation, nclicks_yes, nclicks_no, nbs_btn):
    content_dl = None
    global gdev_mode
    global current_mapelites
    global gen_counter
    global my_emitterslist
    global selected_bins
    global exp_n
    global rngseed
    global consent_ok
    global n_spaceships_inspected
    global time_elapsed
    
    metrics_dl = None
    nbs_err_modal_show = False
    
    ctx = dash.callback_context

    if not ctx.triggered:
        event_trig = None
    else:
        event_trig = ctx.triggered[0]['prop_id'].split('.')[0]

    if event_trig == 'step-btn':
        if len(selected_bins) > 0:
            s = time.perf_counter()
            res = _apply_step(mapelites=current_mapelites,
                              selected_bins=[(x[1], x[0]) for x in selected_bins],
                              gen_counter=gen_counter)
            if res:
                elapsed = time.perf_counter() - s
                gen_counter += 1
                curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                            pop_name=pop_name,
                                            metric_name=metric_name,
                                            method_name=method_name)
                if consent_ok:
                    n_spaceships_inspected.add(1)
                    time_elapsed.add(elapsed)
        else:
            logging.getLogger('webapp').info(msg=f'Step not applied: no bin(s) selected.')
            nbs_err_modal_show = True
            
    elif event_trig == 'reset-btn':
        res = _apply_reset(mapelites=current_mapelites)
        if res:
            gen_counter = 0
            curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                          pop_name=pop_name,
                                          metric_name=metric_name,
                                          method_name=method_name)
            
            if consent_ok:
                n_spaceships_inspected.reset()
                time_elapsed.reset()
    elif event_trig in ['bc0-Major-axis_Medium-axis', 'bc0-Major-axis_Smallest-axis', 'bc0-Average-Proportions', 'bc0-Symmetry']:
        b0 = event_trig.replace('bc0-', '').replace('_', ' / ').replace('-', ' ')
        res = _apply_bc_change(bcs=(b0, b1),
                               mapelites=current_mapelites)
        if res:
            curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                          pop_name=pop_name,
                                          metric_name=metric_name,
                                          method_name=method_name)
    elif event_trig in ['bc1-Major-axis_Medium-axis', 'bc1-Major-axis_Smallest-axis', 'bc1-Average-Proportions', 'bc1-Symmetry']:
        b1 = event_trig.replace('bc1-', '').replace('_', ' / ').replace('-', ' ')
        res = _apply_bc_change(bcs=(b0, b1),
                               mapelites=current_mapelites)
        if res:
            curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                          pop_name=pop_name,
                                          metric_name=metric_name,
                                          method_name=method_name)
    elif event_trig == 'subdivide-btn':
        res = _apply_bin_subdivision(mapelites=current_mapelites,
                                     selected_bins=selected_bins)
        if res:
            curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                          pop_name=pop_name,
                                          metric_name=metric_name,
                                          method_name=method_name)
            selected_bins = []
    elif event_trig == 'lsystem-modules':
        res = _apply_modules_update(mapelites=current_mapelites,
                                    modules=modules)
    elif event_trig == 'update-rules-btn':
        res = _apply_rules_update(mapelites=current_mapelites,
                                  rules=rules)
    # event_trig is a str of a dict, ie: '{"index":*,"type":"fitness-sldr"}', go figure
    elif event_trig is not None and 'fitness-sldr' in event_trig:
        res = _apply_fitness_reweight(mapelites=current_mapelites,
                                      weights=weights)
        if res:
            curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                     pop_name=pop_name,
                                     metric_name=metric_name,
                                     method_name=method_name)
    elif event_trig in ['population-feasible', 'population-infeasible', 'metric-fitness', 'metric-age', 'metric-coverage'] or event_trig == 'method-radio':
        if event_trig == 'population-feasible':
            pop_name = 'Feasible'
        elif event_trig == 'population-infeasible':
            pop_name = 'Infeasible'
        elif event_trig == 'metric-fitness':
            metric_name = 'Fitness'
        elif event_trig == 'metric-age':
            metric_name = 'Age'
        elif event_trig == 'metric-coverage':
            metric_name = 'Coverage'
        curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                     pop_name=pop_name,
                                     metric_name=metric_name,
                                     method_name=method_name)
    elif event_trig in ['symmetry-none', 'symmetry-x', 'symmetry-y', 'symmetry-z'] or event_trig == 'symmetry-radio':
        logging.getLogger('webapp').info(msg=f'Updating all solutions to enforce symmetry...')
        if event_trig == 'symmetry-none':
            symm_axis = 'None'
        elif event_trig == 'symmetry-x':
            symm_axis = 'X-axis'
        elif event_trig == 'symmetry-y':
            symm_axis = 'Y-axis'
        elif event_trig == 'symmetry-z':
            symm_axis = 'Z-axis'
        current_mapelites.reassign_all_content(sym_axis=symm_axis[0].lower() if symm_axis != "None" else None,
                                               sym_upper=symm_orientation == 'Upper')
        curr_content = _get_elite_content(mapelites=current_mapelites,
                                          bin_idx=None,
                                          pop=None)
        cs_string = ''
        cs_properties = get_properties_table()
        logging.getLogger('webapp').info(msg=f'Symmetry enforcement completed.')
    elif event_trig == 'heatmap-plot' or event_trig == 'population_dropdown':
        i, j = _from_bc_to_idx(bcs=(clickData['points'][0]['x'],
                                    clickData['points'][0]['y']),
                               mapelites=current_mapelites)
        if current_mapelites.bins[j, i].non_empty(pop='feasible' if pop_name == 'Feasible' else 'infeasible'):
            if (j, i) in [b.bin_idx for b in current_mapelites._valid_bins()]:
                curr_content = _get_elite_content(mapelites=current_mapelites,
                                                bin_idx=(j, i),
                                                pop='feasible' if pop_name == 'Feasible' else 'infeasible')
                if consent_ok:
                    n_spaceships_inspected.add(1)
                if not current_mapelites.enforce_qnt and selected_bins != []:
                    if (i, j) not in selected_bins:
                        selected_bins.append((i, j))
                    else:
                        selected_bins.remove((i, j))
                else:
                    selected_bins = [(i, j)]
                cs_string = ''
                cs_properties = get_properties_table()
                if len(selected_bins) > 0:
                    elite = get_elite(mapelites=current_mapelites,
                                      bin_idx=_switch([selected_bins[-1]])[0],
                                      pop='feasible' if pop_name == 'Feasible' else 'infeasible')
                    cs_string = elite.string
                    cs_properties = get_properties_table(cs=elite)
        else:
            logging.getLogger('webapp').info(msg=f'Empty bin selected ({i}, {j}).')
    elif event_trig == 'selection-btn':
        _ = _apply_bin_selection_toggle(mapelites=current_mapelites)
        if current_mapelites.enforce_qnt and selected_bins:
            selected_bins = [selected_bins[-1]]
    elif event_trig == 'selection-clr-btn':
        logging.getLogger('webapp').info(msg='Cleared bins selection.')
        selected_bins = []
        curr_content = _get_elite_content(mapelites=current_mapelites,
                                          bin_idx=None,
                                          pop=None)
        
        cs_string = ''
        cs_properties = get_properties_table()
    elif event_trig == 'emitter-dropdown':
        _ = _apply_emitter_change(mapelites=current_mapelites,
                                  emitter_name=emitter_name)
    elif event_trig == 'download-btn':
        if cs_string != '':
            
            def write_archive(bytes_io):
                with ZipFile(bytes_io, mode="w") as zf:
                    # with open('./assets/thumb.png', 'rb') as f:
                    #     thumbnail_img = f.read()
                    curr_content = _get_elite_content(mapelites=current_mapelites,
                                                      bin_idx=tuple(_switch([selected_bins[-1]])[0]),
                                                      pop='feasible')
                    thumbnail_img = curr_content.to_image(format="png")
                    zf.writestr('thumb.png', thumbnail_img)
                    elite = get_elite(mapelites=current_mapelites,
                                      bin_idx=_switch([selected_bins[-1]])[0],
                                      pop='feasible' if pop_name == 'Feasible' else 'infeasible')
                    zf.writestr('bp.sbc', convert_structure_to_xml(structure=elite.content, name=f'My Spaceship ({rngseed}) (exp{exp_n})'))
                    zf.writestr(f'spaceship_{rngseed}_exp{exp_n}', cs_string)
            
            content_dl = dcc.send_bytes(write_archive, f'MySpaceship_{rngseed}_exp{exp_n}_gen{gen_counter}.zip')         
            logging.getLogger('webapp').info(f'Your selected spaceship will be downloaded shortly.')
            
            # end of experiment
            if gen_counter == N_GENS_ALLOWED:
                exp_n += 1
                if exp_n >= len(my_emitterslist):
                    curr_heatmap = go.Figure(data=[])
                    selected_bins = []
                    curr_content = _get_elite_content(mapelites=current_mapelites,
                                                    bin_idx=None,
                                                    pop=None)
                    
                    cs_string = ''
                    cs_properties = get_properties_table()
                    if consent_ok:
                        metrics_dl = dict(content=json.dumps({
                            'time_elapsed': time_elapsed.get_averages(),
                            'n_interactions': n_spaceships_inspected.get_averages()
                            }),
                                        filename=f'user_metrics_{rngseed}')
                    else:
                        metrics_dl = None 
                    logging.getLogger('webapp').info(f'Reached end of all experiments! Please go back to the questionnaire to continue the evaluation.')
                    title = 'Spaceships population'
                    curr_heatmap = go.Figure(
                        data=go.Heatmap(
                            z=np.zeros(0, dtype=object),
                            x=np.zeros(0, dtype=object),
                            y=np.zeros(0, dtype=object),
                            hoverongaps=False,
                            ))
                    curr_heatmap.update_layout(title=dict(text=title),
                                            autosize=False,
                                            clickmode='event+select',
                                            paper_bgcolor='rgba(0,0,0,0)',
                                            plot_bgcolor='rgba(0,0,0,0)',
                                            template='plotly_dark')                
                else:
                    logging.getLogger('webapp').info(msg=f'Reached end of experiment {exp_n}! Loading the next experiment...')
                    gen_counter = 0
                    
                    if consent_ok:
                        # with open(my_emitterslist[exp_n], 'r') as f:
                        #     current_mapelites = json_loads(f.read())
                        logging.getLogger('webapp').info(msg='Initializing population; this may take a while...')
                        current_mapelites.reset()
                        logging.getLogger('webapp').info(msg='Initialization completed.')
                        n_spaceships_inspected.new_generation()
                        time_elapsed.new_generation()
                    else:
                        logging.getLogger('webapp').info(msg='Initializing a new population; this may take a while...')
                        current_mapelites.reset()
                        logging.getLogger('webapp').info(msg='Initialization completed.')
                    curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                                pop_name=pop_name,
                                                metric_name=metric_name,
                                                method_name=method_name)
                    selected_bins = []
                    curr_content = _get_elite_content(mapelites=current_mapelites,
                                                    bin_idx=None,
                                                    pop=None)
                    
                    cs_string = ''
                    cs_properties = get_properties_table()
                    logging.getLogger('webapp').info(msg='Next experiment loaded. Please fill out the questionnaire before continuing.')
    elif event_trig == 'popdownload-btn':
        content_dl = dict(content=json.dumps([b.to_json() for b in current_mapelites.bins.flatten().tolist()]),
                          filename=f'population_{rngseed}_exp{exp_n}.json')
    elif event_trig == 'popupload-data':
        _, upload_contents = upload_contents.split(',')
        upload_contents = base64.b64decode(upload_contents).decode()
        all_bins = np.asarray([MAPBin.from_json(x) for x in json.loads(upload_contents)])
        current_mapelites.reset(lcs=[])
        all_bins = all_bins.reshape(current_mapelites.bin_qnt)
        current_mapelites.bins = all_bins
        current_mapelites.reassign_all_content()
        logging.getLogger('webapp').info(msg=f'Set population from file successfully.')
        curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                    pop_name=pop_name,
                                    metric_name=metric_name,
                                    method_name=method_name)
    elif event_trig == 'consent-yes' or event_trig == 'consent-no':
        consent_ok = True if nclicks_yes else False if nclicks_no else None
        if nclicks_yes:
            logging.getLogger('webapp').info(msg=f'Thank you for participating in the user study! Please do not refresh the page.')
            # TODO: this should load the first mapelites JSON
            # with open(my_emitterslist[exp_n], 'r') as f:
            #     current_mapelites = json_loads(f.read())
            logging.getLogger('webapp').info(msg='Initializing population; this may take a while...')
            current_mapelites.reset()
            logging.getLogger('webapp').info(msg='Initialization completed.')
        else:
            logging.getLogger('webapp').info(msg=f'No user data will be collected during this session. Please do not refresh the page.')
            logging.getLogger('webapp').info(msg='Initializing population; this may take a while...')
            current_mapelites.reset()
            logging.getLogger('webapp').info(msg='Initialization completed.')
        privacy_modal_show = False
        qs_modal_show = True
        gen_counter = 0
        curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                        pop_name=pop_name,
                                        metric_name=metric_name,
                                        method_name=method_name)
    elif event_trig == 'nbs-err-btn':
        nbs_err_modal_show = False
    elif event_trig is None:
        curr_heatmap = _build_heatmap(mapelites=current_mapelites,
                                    pop_name=pop_name,
                                    metric_name=metric_name,
                                    method_name=method_name)
        curr_content = _get_elite_content(mapelites=current_mapelites,
                                          bin_idx=None,
                                          pop=None)
    else:
        logging.getLogger('webapp').info(msg=f'Unrecognized event trigger: {event_trig}. No operations have been applied!')

    selected_bins, selected_bins_str = format_bins(mapelites=current_mapelites,
                                                   bins_idx_list=selected_bins,
                                                   do_switch=True,
                                                   str_prefix='Selected bin(s):',
                                                   filter_out_empty=True) 
    _, valid_bins_str = format_bins(mapelites=current_mapelites,
                                    do_switch=False,
                                    bins_idx_list=_get_valid_bins(mapelites=current_mapelites),
                                    str_prefix='Valid bins are:',
                                    filter_out_empty=False)
    
    #   ('heatmap-plot', 'figure'),
    #   ('content-plot', 'figure'),
    #   ('valid-bins', 'children'),
    #   ('gen-display', 'children'),
    #   ('hl-rules', 'value'),
    #   ('selected-bin', 'children'),
    #   ('content-string', 'value'),
    #   ('spaceship-properties', 'children'),
    #   ('download-btn', 'disabled'),
    #   ('step-btn', 'disabled'),
    #   ("download-content", "data"),
    #   ("download-metrics", "data"),
    #   ('population-dropdown', 'label'),
    #   ('metric-dropdown', 'label'),
    #   ('b0-dropdown', 'label'),
    #   ('b1-dropdown', 'label'),
    #   ('symmetry-dropdown', 'label'),
    #   ("consent-modal", "is_open"),
    #   ("quickstart-modal", "is_open"),
    #   ("nbs-err-modal", "is_open")
    return curr_heatmap,\
        curr_content,\
        valid_bins_str,\
        f'Current generation: {gen_counter}',\
        str(current_mapelites.lsystem.hl_solver.parser.rules),\
        selected_bins_str,\
        cs_string,\
        cs_properties,\
        False,\
        not gdev_mode and gen_counter >= N_GENS_ALLOWED,\
        content_dl,\
        metrics_dl,\
        pop_name,\
        metric_name,\
        b0,\
        b1,\
        symm_axis,\
        privacy_modal_show,\
        qs_modal_show,\
        nbs_err_modal_show
