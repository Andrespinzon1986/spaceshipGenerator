import json
import logging
from datetime import datetime
from typing import Dict, List, Tuple

import dash
from dash.dependencies import Input, Output, State
from dash import ALL
from dash.exceptions import PreventUpdate
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from dash import dcc, html
from pcgsepy.lsystem.rules import StochasticRules
from pcgsepy.lsystem.solution import CandidateSolution
from pcgsepy.common.jsonifier import json_dumps, json_loads

from ..config import BIN_POP_SIZE, CS_MAX_AGE
from ..mapelites.map import MAPElites


# TODO: Update MAPElites object during the callbacks


class DashLoggerHandler(logging.StreamHandler):
    def __init__(self):
        logging.StreamHandler.__init__(self)
        self.queue = []

    def emit(self, record):
        t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = self.format(record)
        self.queue.append(f'[{t}]\t{msg}')


logger = logging.getLogger('dash-msgs')
logger.setLevel(logging.DEBUG)
dashLoggerHandler = DashLoggerHandler()
logger.addHandler(dashLoggerHandler)


hm_callback_props = {}


def set_callback_props(mapelites: MAPElites):
    hm_callback_props['pop'] = {
        'Feasible': 'feasible',
        'Infeasible': 'infeasible'
    }
    hm_callback_props['metric'] = {
        'Fitness': {
            'name': 'fitness',
            'zmax': {
                'feasible': sum([x.weight * x.bounds[1] for x in mapelites.feasible_fitnesses]) + 0.5,
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


description_str, help_str = '', ''
with open('./assets/description.md', 'r') as f:
    description_str = f.read()
with open('./assets/help.md', 'r') as f:
    help_str = f.read()

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

app = dash.Dash(__name__,
                title='SE ICMAP-Elites',
                external_stylesheets=[
                    'https://codepen.io/chriddyp/pen/bWLwgP.css'],
                update_title=None)


def set_app_layout(mapelites: MAPElites):
    behavior_descriptors_names = [b.name for b in mapelites.b_descs]

    app.layout = html.Div(children=[
        # HEADER
        html.Div(children=[
            html.H1(children='🚀Space Engineers🚀 IC MAP-Elites',
                    className='title'),
            dcc.Markdown(children=description_str,
                         className='page-description'),
        ],
            className='header'),
        html.Br(),
        # BODY
        html.Div(children=[
            # PLOTS
            html.Div(children=[
                # HEATMAP
                html.Div(children=[
                    dcc.Graph(id="heatmap-plot",
                              figure=go.Figure(data=[]))
                ],
                    className='heatmap-div'),
                # CONTENT PLOT
                html.Div(children=[
                    dcc.Graph(id="content-plot",
                              figure=go.Figure(data=[])),
                ],
                    className='content-div'),
            ],
                className='plots'),
            # PROPERTIES & DOWNLOAD
            html.Div(children=[
                html.H6('Content properties',
                        className='section-title'),
                html.Div(children=[
                    html.P(children='Spaceship size: ',
                           className='properties-text',
                           id='spaceship-size'),
                    html.P(children='Number of blocks: ',
                           className='properties-text',
                           id='n-blocks'),
                    html.P(children='Content string:',
                           className='properties-text'),
                    dcc.Textarea(id='content-string',
                                    value='',
                                    contentEditable=False,
                                    disabled=True,
                                    style={'width': '100%', 'height': 150}),
                    html.Div(children=[
                        html.Button('Download content',
                                    id='download-btn',
                                    className='button-div'),
                        dcc.Download(id='download-content')
                    ],
                        className='button-div')
                ],
                    style={'padding-left': '10px'}),
            ],
                className='properties-div'),
            html.Br(),
            # PLOT CONTROLS
            html.Div(children=[
                html.H6(children='Plot settings',
                        className='section-title'),
                html.P(children='Choose which population to display.',
                       className='generic-description'),
                dcc.Dropdown(['Feasible', 'Infeasible'],
                             'Feasible',
                             id='population-dropdown',
                             className='dropdown'),
                html.Br(),
                html.P(children='Choose which metric to plot.',
                       className='generic-description'),
                dcc.Dropdown(['Fitness', 'Age', 'Coverage'],
                             'Fitness',
                             id='metric-dropdown',
                             className='dropdown'),
                html.Br(),
                html.P(children='Choose whether to compute the metric for the entire bin population or just the elite.',
                       className='generic-description'),
                dcc.RadioItems(['Population', 'Elite'],
                               'Population',
                               id='method-radio',
                               className='radio')
            ],
                className='graph-controls-div'),
            # EXPERIMENT SETTINGS
            html.Div(children=[
                html.H6(children='Experiment settings',
                        className='section-title'),
                html.Div(children=[
                    html.P(children='Valid bins are: ',
                           className='properties-text',
                           id='valid-bins'),
                    html.P(children='Current generation: 0',
                           className='properties-text',
                           id='gen-display'),
                    html.P(children='Selected bin(s): []',
                           className='properties-text',
                           id='selected-bin')
                ],
                    style={'margin-left': '10px'}),
                html.H6(children='Choose feature descriptors (X, Y):',
                        className='section-title'),
                html.Div(children=[
                    html.Div(children=[
                        dcc.Dropdown(behavior_descriptors_names,
                                     mapelites.b_descs[0].name,
                                     id='b0-dropdown',
                                     className='dropdown')
                    ],
                        style={'width': '50%'}),
                    html.Div(children=[
                        dcc.Dropdown(behavior_descriptors_names,
                                     mapelites.b_descs[1].name,
                                     id='b1-dropdown',
                                     className='dropdown')
                    ],
                        style={'width': '50%'}),
                ],
                    style={'display': 'flex', 'text-align': 'center', 'margin-left': '10px'}),
                html.H6(children='Toggle L-system modules',
                        className='section-title'),
                html.Div(children=[
                    dcc.Checklist(id='lsystem-modules',
                                  options=[
                                      x.name for x in mapelites.lsystem.modules],
                                  value=[
                                      x.name for x in mapelites.lsystem.modules if x.active],
                                  inline=True,
                                  className='checkboxes'
                                  )],
                         style={'text-align': 'center'}
                         ),
                html.H6(children='Control fitness weights',
                        className='section-title'),
                html.Div(children=[
                    html.Div(children=[
                        html.P(children=f.name,
                               className='generic-description'),
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
                        )],
                        style={'width': '80%', 'vertical-align': 'middle', 'margin': '0 auto',
                               'display': 'grid', 'grid-template-columns': '40% 60%'}
                    ) for i, f in enumerate(mapelites.feasible_fitnesses)
                ]),
            ],
                className='experiment-controls-div'),
            # EXPERIMENT CONTROLS
            html.Div(children=[
                html.H6('Experiment controls',
                        className='section-title'),
                html.Div(children=[
                    html.Button(children='Apply step',
                                id='step-btn',
                                n_clicks=0,
                                className='button')
                ],
                    className='button-div'),
                html.Br(),
                html.Div(children=[
                    html.Button(children='Initialize/Reset',
                                id='reset-btn',
                                n_clicks=0,
                                className='button')
                ],
                    className='button-div'),
                html.Br(),
                html.Div(children=[
                    html.Button(children='Clear selection',
                                id='selection-clr-btn',
                                n_clicks=0,
                                className='button')
                ],
                    className='button-div'),
                html.Br(),
                html.Div(children=[
                    html.Button(children='Toggle single bin selection',
                                id='selection-btn',
                                n_clicks=0,
                                className='button')
                ],
                    className='button-div'),
                html.Br(),
                html.Div(children=[
                    html.Button(children='Subdivide selected bin(s)',
                                id='subdivide-btn',
                                n_clicks=0,
                                className='button')
                ],
                    className='button-div'),
            ],
                className='experiment-controls-div'),
            # RULES
            html.Div(children=[
                html.H6(children='High-level rules',
                        className='section-title'),
                dcc.Textarea(id='hl-rules',
                                value=str(
                                    mapelites.lsystem.hl_solver.parser.rules),
                                wrap='False',
                                style={'width': '100%', 'height': 250}),
                html.Div(children=[
                    html.Button(children='Update high-level rules',
                                id='update-rules-btn',
                                n_clicks=0,
                                className='button')
                ],
                    className='button-div'),
            ],
                # style={'width': '30%', 'display': 'inline-block', 'vertical-align': 'top'}),
                className='experiment-controls-div'),
        ],
            className='body-div'),
        html.Br(),
        html.Div(children=[
            # LOG
            html.Div(children=[
                dcc.Interval(id='interval1',
                                interval=1 * 1000,
                                n_intervals=0),
                html.H6(children='Log',
                        className='section-title'),
                dcc.Textarea(id='console-out',
                                value='',
                                wrap='False',
                                contentEditable=False,
                                disabled=True,
                                style={'width': '100%', 'height': 300})
            ],
                style={'width': '30%', 'display': 'inline-block', 'vertical-align': 'top'})
        ]),
        html.Br(),
        # FOOTER
        html.Div(children=[
            html.H6(children='Help',
                    className='section-title'),
            dcc.Markdown(help_str,
                         className='page-description')
        ],
            className='footer'),
        # client-side storage
        dcc.Store(id='gen-counter'),
        dcc.Store(id='selected-bins'),
        dcc.Store(id='mapelites', data=json_dumps(obj=mapelites))
    ])


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


def _switch(ls: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    res = []
    for e in ls:
        res.append((e[1], e[0]))
    return res


def _get_valid_bins(mapelites: MAPElites):
    valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
    return _switch(valid_bins)


def _build_heatmap(mapelites: MAPElites,
                   pop_name: str,
                   metric_name: str,
                   method_name: str,
                   valid_bins: List[Tuple[int, int]]) -> go.Figure:    
    metric = hm_callback_props['metric'][metric_name]
    use_mean = hm_callback_props['method'][method_name]
    population = hm_callback_props['pop'][pop_name]
    # build hotmap
    disp_map = np.zeros(shape=mapelites.bins.shape)
    text = []
    for i in range(mapelites.bins.shape[0]):
        for j in range(mapelites.bins.shape[1]):
            v = mapelites.bins[i, j].get_metric(metric=metric['name'],
                                                use_mean=use_mean,
                                                population=population)
            disp_map[i, j] = v  # if v > 0 else None
            s = str((j, i)) if (i, j) in valid_bins else ''
            if j == 0:
                text.append([s])
            else:
                text[-1].append(s)
    # plot
    x_labels = np.cumsum([0] + mapelites.bin_sizes[0]
                         [:-1]) + mapelites.b_descs[0].bounds[0]
    y_labels = np.cumsum([0] + mapelites.bin_sizes[1]
                         [:-1]) + mapelites.b_descs[1].bounds[0]
    title = f'{pop_name} population {metric_name.lower()} ({"Average" if use_mean else "Elite"})'
    heatmap = go.Figure(data=go.Heatmap(
        z=disp_map,
        zmin=0,
        zmax=hm_callback_props['metric'][metric_name]['zmax'][population],
        x=x_labels,
        y=y_labels,
        hoverongaps=False,
        colorscale=hm_callback_props['metric'][metric_name]['colorscale'],
        text=text,
        texttemplate="%{text}",
        textfont={"color": 'rgba(0, 255, 0, 0.5)'},
    ))
    heatmap.update_xaxes(title=dict(text=mapelites.b_descs[0].name))
    heatmap.update_yaxes(title=dict(text=mapelites.b_descs[1].name))
    heatmap.update_coloraxes(colorbar_title_text=metric_name)
    heatmap.update_layout(title=dict(text=title),
                          autosize=False,
                          clickmode='event+select',
                          paper_bgcolor='rgba(0,0,0,0)',
                          plot_bgcolor='rgba(0,0,0,0)')
    hovertemplate = f'{mapelites.b_descs[0].name}: X<br>{mapelites.b_descs[1].name}: Y<br>{metric_name}: Z<extra></extra>'
    hovertemplate = hovertemplate.replace(
        'X', '%{x}').replace('Y', '%{y}').replace('Z', '%{z}')
    heatmap.update_traces(hovertemplate=hovertemplate,
                          selector=dict(type='heatmap'))
    heatmap.update_layout(
        xaxis={
            # 'tickmode': 'linear',
            # 'tick0': 0,
            # 'dtick': mapelites.bin_sizes[0]
            'tickvals': x_labels
        },
        yaxis={
            # 'tickmode': 'linear',
            # 'tick0': 0,
            # 'dtick': mapelites.bin_sizes[1]
            'tickvals': y_labels
        }
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
    # get elite content
    elite = mapelites.get_elite(bin_idx=bin_idx,
                                pop=pop).content
    structure = elite.as_grid_array()
    arr = np.nonzero(structure)
    x, y, z = arr
    cs = [structure[i, j, k] for i, j, k in zip(x, y, z)]
    ss = [elite._clean_label(elite.ks[v - 1]) for v in cs]
    fig = px.scatter_3d(x=x,
                        y=y,
                        z=z,
                        color=ss,
                        color_discrete_map=_get_colour_mapping(ss),
                        labels={
                            'x': 'x',
                            'y': 'y',
                            'z': 'z',
                            'color': 'Block type'
                        },
                        title='Last clicked elite content')
    fig.update_layout(scene=dict(aspectmode='data'),
                      paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(0,0,0,0)')
    return fig


@app.callback(Output('console-out', 'value'),
              Input('interval1', 'n_intervals'))
def update_output(n):
    return ('\n'.join(dashLoggerHandler.queue))


@app.callback(Output('selected-bin', 'children'),
              Output('content-plot', 'figure'),
              Output('selected-bins', 'data'),
              Output('content-string', 'value'),
              Output('spaceship-size', 'children'),
              Output('n-blocks', 'children'),
              Input('heatmap-plot', 'clickData'),
              Input('population-dropdown', 'value'),
              Input('selection-btn', 'n_clicks'),
              Input('selection-clr-btn', 'n_clicks'),
              State('selected-bin', 'children'),
              State('content-plot', 'figure'),
              State('selected-bins', 'data'),
              State('spaceship-size', 'children'),
              State('n-blocks', 'children'),
              State('mapelites', 'data'),
              prevent_initial_call=True,)
def display_click_data(clickData, pop_name, selection_btn, clear_btn,
                       curr_selected, curr_fig, selected_bins, curr_size, curr_n_blocks, mapelites):
    selected_bins = json.loads(selected_bins) if selected_bins else []
    if len(selected_bins) > 0:
        selected_bins = [(x[0], x[1]) for x in selected_bins if x != []]

    mapelites = json_loads(s=mapelites)

    ctx = dash.callback_context

    if not ctx.triggered:
        event_trig = None
    else:
        event_trig = ctx.triggered[0]['prop_id'].split('.')[0]

    if event_trig == 'heatmap-plot' or event_trig == 'population_dropdown':
        i, j = _from_bc_to_idx(bcs=(clickData['points'][0]['x'],
                                    clickData['points'][0]['y']),
                               mapelites=mapelites)
        if mapelites.non_empty(
                bin_idx=(j, i),
                pop='feasible' if pop_name == 'Feasible' else 'infeasible'):
            fig = _get_elite_content(
                mapelites=mapelites,
                bin_idx=(j, i),
                pop='feasible' if pop_name == 'Feasible' else 'infeasible')
            if not mapelites.enforce_qnt and curr_selected != 'Selected bin(s): []':
                if (i, j) not in selected_bins:
                    selected_bins.append((i, j))
                else:
                    selected_bins.remove((i, j))
            else:
                selected_bins = [(i, j)]
            if len(selected_bins) > 0:
                v = "; ".join([str(x) for x in selected_bins])
            else:
                v = '[]'
            selected_bins = [[int(x[0]), int(x[1])] for x in selected_bins]
            content_str = ''
            ss_size, n_blocks = '', ''
            if len(selected_bins) > 0:
                b = selected_bins[-1]
                b = (b[1], b[0])
                elite = mapelites.get_elite(bin_idx=b,
                                            pop='feasible' if pop_name == 'Feasible' else 'infeasible')
                content_str = elite.string
                ss_size = elite.content._max_dims
                n_blocks = len(elite.content._blocks.keys())
            return f'Selected bin(s): {v}', fig, json.dumps(selected_bins), content_str, f'Spaceship size: {ss_size}', f'Number of blocks: {n_blocks}'
        else:
            logging.getLogger('dash-msgs').error(
                msg=f'Empty bin selected ({i}, {j}).')
            raise PreventUpdate

    elif event_trig == 'selection-btn':
        mapelites.enforce_qnt = not mapelites.enforce_qnt
        logging.getLogger(
            'dash-msgs').debug(msg=f'MAP-Elites single bin selection set to {mapelites.enforce_qnt}.')
        if mapelites.enforce_qnt:
            selected_bins = [selected_bins[-1]] if selected_bins else []
        if len(selected_bins) > 0:
            v = "; ".join([str(x) for x in selected_bins])
        else:
            v = '[]'
        content_str = ''
        ss_size, n_blocks = '', ''
        if len(selected_bins) > 0:
            b = selected_bins[-1]
            b = (b[1], b[0])
            elite = mapelites.get_elite(bin_idx=b,
                                        pop='feasible' if pop_name == 'Feasible' else 'infeasible')
            content_str = elite.string
            ss_size = elite.content._max_dims
            n_blocks = len(elite.content._blocks.keys())
        selected_bins = [[int(x[0]), int(x[1])] for x in selected_bins]
        curr_fig = curr_fig if curr_fig is not None else go.Figure(data=[])
        return f'Selected bin(s): {v}', curr_fig, json.dumps(selected_bins), content_str, f'Spaceship size: {ss_size}', f'Number of blocks: {n_blocks}'

    elif event_trig == 'selection-clr-btn':
        logging.getLogger('dash-msgs').debug(msg='Cleared bins selection.')
        selected_bins = []
        # curr_fig = curr_fig if curr_fig is not None else go.Figure(data=[])
        curr_fig = go.Figure(data=[])
        return f'Selected bin(s): {selected_bins}', curr_fig, json.dumps(selected_bins), '', 'Spaceship size: ', 'Number of blocks: '

    else:
        raise PreventUpdate


@app.callback(Output('heatmap-plot', 'figure'),
              Output('valid-bins', 'children'),
              Output('gen-display', 'children'),
              Output('gen-counter', 'data'),
              State('heatmap-plot', 'figure'),
              Input('population-dropdown', 'value'),
              Input('metric-dropdown', 'value'),
              Input('method-radio', 'value'),
              Input('step-btn', 'n_clicks'),
              Input('reset-btn', 'n_clicks'),
              Input('subdivide-btn', 'n_clicks'),
              Input({'type': 'fitness-sldr', 'index': ALL}, 'value'),
              State('selected-bins', 'data'),
              State('gen-counter', 'data'),
              State('mapelites', 'data'),
              Input('b0-dropdown', 'value'),
              Input('b1-dropdown', 'value'))
def update_heatmap(curr_heatmap, pop_name, metric_name, method_name,
                   n_clicks_step, n_clicks_reset, n_clicks_sub, weights, selected_bins, gen_counter,
                   mapelites, b0, b1):
    gen_counter = json.loads(gen_counter) if gen_counter else 0
    selected_bins = json.loads(selected_bins) if selected_bins else []
    selected_bins = [(x[1], x[0]) for x in selected_bins]

    mapelites = json_loads(s=mapelites)

    ctx = dash.callback_context

    if not ctx.triggered:
        event_trig = None
    else:
        event_trig = ctx.triggered[0]['prop_id'].split('.')[0]

    if event_trig == 'step-btn':
        if len(selected_bins) > 0:
            valid = True
            if mapelites.enforce_qnt:
                valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
                for bin_idx in selected_bins:
                    valid &= bin_idx in valid_bins
            if valid:
                logging.getLogger('dash-msgs').debug(
                    msg=f'Started step {gen_counter}...')
                mapelites._interactive_step(bin_idxs=selected_bins,
                                            gen=gen_counter)
                gen_counter += 1
                logging.getLogger('dash-msgs').debug(
                    msg=f'Completed step {gen_counter - 1}.')

                logging.getLogger(
                    'dash-msgs').debug(msg='Started shadow step(s)...')
                mapelites.shadow_steps(gen=gen_counter - 1,
                                       n_steps=2)
                logging.getLogger(
                    'dash-msgs').debug(msg='Shadow step(s) completed.')

                valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
                curr_heatmap = _build_heatmap(mapelites=mapelites,
                                              pop_name=pop_name,
                                              metric_name=metric_name,
                                              method_name=method_name,
                                              valid_bins=valid_bins)
            else:
                logging.getLogger('dash-msgs').error(
                    msg='Step not applied: invalid bin(s) selected.')
        return curr_heatmap, f'Valid bins are: {_get_valid_bins(mapelites=mapelites)}', f'Current generation: {gen_counter}', json.dumps(
            gen_counter)
    elif event_trig == 'reset-btn':
        logging.getLogger('dash-msgs').debug(
            msg='Started resetting all bins...')
        gen_counter = 0
        mapelites.reset()
        valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
        heatmap = _build_heatmap(mapelites=mapelites,
                                 pop_name=pop_name,
                                 metric_name=metric_name,
                                 method_name=method_name,
                                 valid_bins=valid_bins)
        logging.getLogger('dash-msgs').debug(msg='Reset completed.')
        return heatmap, f'Valid bins are: {_get_valid_bins(mapelites=mapelites)}', f'Current generation: {gen_counter}', json.dumps(
            gen_counter)
    elif event_trig == 'b0-dropdown' or event_trig == 'b1-dropdown':
        logging.getLogger(
            'dash-msgs').debug(msg=f'Updating feature descriptors to ({b0}, {b1})...')
        b0 = mapelites.b_descs[[b.name for b in mapelites.b_descs].index(b0)]
        b1 = mapelites.b_descs[[b.name for b in mapelites.b_descs].index(b1)]
        mapelites.update_behavior_descriptors((b0, b1))
        valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
        heatmap = _build_heatmap(mapelites=mapelites,
                                 pop_name=pop_name,
                                 metric_name=metric_name,
                                 method_name=method_name,
                                 valid_bins=valid_bins)
        logging.getLogger('dash-msgs').debug(msg='Update completed.')
        return heatmap, f'Valid bins are: {_get_valid_bins(mapelites=mapelites)}', f'Current generation: {gen_counter}', json.dumps(
            gen_counter)
    elif event_trig == 'subdivide-btn':
        bin_idxs = [(x[1], x[0]) for x in selected_bins]
        for bin_idx in bin_idxs:
            mapelites.subdivide_range(bin_idx=bin_idx)
        valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
        heatmap = _build_heatmap(mapelites=mapelites,
                                 pop_name=pop_name,
                                 metric_name=metric_name,
                                 method_name=method_name,
                                 valid_bins=valid_bins)
        logging.getLogger(
            'dash-msgs').debug(msg=f'Subdivided bin(s): {selected_bins}.')
        return heatmap, f'Valid bins are: {_get_valid_bins(mapelites=mapelites)}', f'Current generation: {gen_counter}', json.dumps(
            gen_counter)
    # event_trig is a str of a dict, ie: '{"index":*,"type":"fitness-sldr"}', go figure
    elif event_trig is not None and 'fitness-sldr' in event_trig:
        mapelites.update_fitness_weights(weights=weights)
        logging.getLogger(
            'dash-msgs').warning(msg='Updated fitness functions weights.')
        hm_callback_props['metric']['Fitness']['zmax']['feasible'] = sum(
            [x.weight * x.bounds[1] for x in mapelites.feasible_fitnesses]) + 0.5
        valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
        heatmap = _build_heatmap(mapelites=mapelites,
                                 pop_name=pop_name,
                                 metric_name=metric_name,
                                 method_name=method_name,
                                 valid_bins=valid_bins)
        return heatmap, f'Valid bins are: {_get_valid_bins(mapelites=mapelites)}', f'Current generation: {gen_counter}', json.dumps(
            gen_counter)
    else:
        valid_bins = [x.bin_idx for x in mapelites._valid_bins()]
        heatmap = _build_heatmap(mapelites=mapelites,
                                 pop_name=pop_name,
                                 metric_name=metric_name,
                                 method_name=method_name,
                                 valid_bins=valid_bins)
        return heatmap, f'Valid bins are: {_get_valid_bins(mapelites=mapelites)}', f'Current generation: {gen_counter}', json.dumps(
            gen_counter)


@app.callback(
    Output('lsystem-modules', 'value'),
    State('mapelites', 'data'),
    Input('lsystem-modules', 'value'),
    prevent_initial_call=True,
)
def toggle_lsystem_modules(mapelites, modules):
    mapelites = json_loads(s=mapelites)
    all_modules = [x for x in mapelites.lsystem.modules]
    names = [x.name for x in all_modules]
    for i, module in enumerate(names):
        if module in modules and not all_modules[i].active:
            # activate module
            mapelites.toggle_module_mutability(module=module)
            logging.getLogger('dash-msgs').debug(msg=f'Enabled {module}.')
            break
        elif module not in modules and all_modules[i].active:
            # deactivate module
            mapelites.toggle_module_mutability(module=module)
            logging.getLogger('dash-msgs').debug(msg=f'Disabled {module}.')
            break
    return modules


@app.callback(
    Output("download-content", "data"),
    Input("download-btn", "n_clicks"),
    State('content-string', 'value'),
    prevent_initial_call=True,
)
def download_content(n_clicks,
                     content_string):
    if content_string != '':
        return dict(content=content_string, filename='MySpaceship.txt')


@app.callback(
    Output('hl-rules', 'value'),
    Input('update-rules-btn', 'n_clicks'),
    State('mapelites', 'data'),
    State('hl-rules', 'value'),
    prevent_initial_call=True
)
def update_lsystem_hl_rules(n_clicks,
                            mapelites,
                            rules):
    mapelites = json_loads(s=mapelites)
    new_rules = StochasticRules()
    for rule in rules.split('\n'):
        lhs, p, rhs = rule.strip().split(' ')
        new_rules.add_rule(lhs=lhs,
                           rhs=rhs,
                           p=float(p))
    try:
        new_rules.validate()
        mapelites.lsystem.hl_solver.parser.rules = new_rules
        logging.getLogger('dash-msgs').debug(msg=f'L-system rules updated.')
    except AssertionError as e:
        logging.getLogger(
            'dash-msgs').warning(msg=f'Failed updating L-system rules ({e}).')
    return str(mapelites.lsystem.hl_solver.parser.rules)