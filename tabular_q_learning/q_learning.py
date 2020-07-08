# Copyright (c) 2019–2020, The Regents of the University of California.
# All rights reserved.
#
# This file is based on Denny Britz's implementation of (tabular) Q-Learning,
# available at:
#
# https://github.com/dennybritz/reinforcement-learning/blob/master/TD/Q-Learning%20Solution.ipynb
#
# The code in this file allows using Q-Learning with the Safety Bellman Equation
# (SBE) from Equation (7) in [ICRA19].
#
# This file is subject to the terms and conditions defined in the LICENSE file
# included in this code repository.
#
# Please contact the author(s) of this library if you have any questions.
# Authors: Neil Lugovoy   ( nflugovoy@berkeley.edu )

import sys
import numpy as np
import time
from utils import discrete_to_real, discretize_state, sbe_outcome, save
from datetime import datetime

def learn(
        get_learning_rate, get_epsilon, get_gamma, max_episodes, buckets, state_bounds, env,
        max_episode_length=None, q_values=None, start_episode=None, suppress_print=False, seed=0,
        fictitious_terminal_val=None, use_sbe=True, save_freq=None):
    """

    :param get_learning_rate: function of current episode number returns learning rate
    :param get_epsilon: function of current episode number returns explore rate
    :param get_gamma: a function of current episode returns gamma, remember to set gamma to None if
    using this
    :param max_episodes: maximum number of episodes to run for
    :param buckets: tuple of ints where the ith value is the number of buckets for ith dimension of
    state
    :param state_bounds: list of tuples where ith tuple contains the min and max value in that order
    of ith dimension
    :param env: Open AI gym environment
    :param max_episode_length: number of timesteps that counts as solving an episode, also acts as
    max episode timesteps
    :param q_values: precomputed q_values function for warm start
    :param start_episode: what episode to start hyper-parameter schedulers at if warmstart
    :param suppress_print: boolean whether to suppress print statements about current episode or not
    :param seed: seed for random number generator
    :param fictitious_terminal_val: whether to use a terminal state with this value for the backup
    when a trajectory ends. this is helpful because it avoids having a ring of terminal states
    around the failure set. note that every terminal trajectory will use this as the value for the
    backup
    :param use_sbe: whether to use the Safety Bellman Equation backup from equation (7) in [ICRA19]. If false
    the standard sum of discounted rewards backup is used.
    :param save_freq: how often to save q_values and stats
    :return: q_values, a numpy tensor of shape (buckets + (env.action_space.n,)) that contains the
    q_values value function
    for example in cartpole the dimensions are q_values[x][x_dot][theta][theta_dot][action]
    """
    start = time.process_time()  # used for time performance analysis
    now = datetime.now()
    np.random.seed(seed)

    # argument checks
    if max_episode_length is None:
        import warnings
        warnings.warn("max_episode_length is None assuming infinite episode length")

    # set up q_values
    if q_values is None:
        if start_episode is not None:
            raise ValueError("start_episode is only to be used with a warmstart q_values")

        q_values = np.zeros(buckets + (env.action_space.n,))
        # need to use multi index to iterate over variable rank tensor
        it = np.nditer(q_values, flags=['multi_index'])
        while not it.finished:
            # initialize q_values to l values for warm start
            state = it.multi_index[:-1]  # chop off action from q index to get state
            q_values[it.multi_index] = env.l_function(discrete_to_real(buckets,
                                                                   state_bounds,
                                                                   state))
            it.iternext()

    elif not np.array_equal(np.shape(q_values)[:-1], buckets):
        raise ValueError("The shape of q_values excluding the last dimension must be the same as "
                         "the shape of the discretization of states.")
    elif start_episode is None:
        import warnings
        warnings.warn("used warm start q_values without a start_episode, hyper-parameter "
                      "schedulers may produce undesired results")
    # setting up stats
    stats = {
        "start_time": datetime.now().strftime("%b_%d_%y %H:%M:%S"),
        "episode_lengths": np.zeros(max_episodes),
        "average_episode_rewards": np.zeros(max_episodes),
        "true_min": np.zeros(max_episodes),
        "episode_outcomes": np.zeros(max_episodes),
        "state_action_visits": np.zeros(np.shape(q_values)),
        "epsilon": np.zeros(max_episodes),
        "learning_rate": np.zeros(max_episodes),
        "state_bounds": state_bounds,
        "buckets": buckets,
        "gamma": np.zeros(max_episodes),
        "type": "tabular q_values-learning",
        "environment": env.spec.id,
        "seed": seed,
        "episode": 0,
    }

    env.set_discretization(buckets=buckets, bounds=state_bounds)

    if start_episode is None:
        start_episode = 0

    # set starting exploration fraction, learning rate, and discount factor
    epsilon = get_epsilon(start_episode, 1)
    alpha = get_learning_rate(start_episode, 1)
    gamma = get_gamma(start_episode, 1)

    # main loop
    for episode in range(max_episodes):
        if not suppress_print and (episode + 1) % 100 == 0:
            message = "\rEpisode {}/{} alpha:{} gamma:{} epsilon:{}."
            print(message.format(episode + 1, max_episodes, alpha, gamma, epsilon), end="")
            sys.stdout.flush()
        state_real_valued = env.reset()
        state = discretize_state(buckets, state_bounds, env.bins, state_real_valued)
        done = False
        t = 0
        episode_rewards = []
        while not done:
            # determine action to use based on epsilon greedy schedule
            action = select_action(q_values, state, epsilon)

            # take step and discretize state
            next_state_real_valued, reward, done, _ = env.step(action)
            next_state = discretize_state(buckets, state_bounds, env.bins, next_state_real_valued)

            # update episode statistics
            stats['state_action_visits'][state + (action,)] += 1
            num_visits = stats['state_action_visits'][state + (action,)]
            episode_rewards.append(reward)
            t += 1

            # update exploration fraction, learning rate, and discount factor
            epsilon = get_epsilon(episode + start_episode, num_visits)
            alpha = get_learning_rate(episode + start_episode, num_visits)
            gamma = get_gamma(episode + start_episode, num_visits)

            # perform bellman update and move along state variables
            if use_sbe:  # Safety Bellman Equation backup
                if fictitious_terminal_val:
                    q_terminal = (1.0 - gamma) * reward + \
                                 gamma * min(reward, fictitious_terminal_val)
                else:
                    q_terminal = reward
                q_non_terminal = (1.0 - gamma) * reward + \
                                gamma * min(reward, np.amax(q_values[next_state]))
            else:  # sum of discounted rewards backup
                if fictitious_terminal_val:
                    q_terminal = reward + gamma * fictitious_terminal_val
                else:
                    q_terminal = reward
                q_non_terminal = reward + gamma * np.amax(q_values[next_state])

            # update q values
            new_q = done * q_terminal + (1.0 - done) * q_non_terminal
            q_values[state + (action,)] = (1 - alpha) * q_values[state + (action,)] + alpha * new_q
            state = next_state

            # end episode if max episode length reached
            if max_episode_length is not None and t >= max_episode_length:
                break

        # save episode statistics
        episode_rewards = np.array(episode_rewards)
        outcome = sbe_outcome(episode_rewards, gamma)[0]
        stats["episode_outcomes"][episode] = outcome
        stats["true_min"][episode] = np.min(episode_rewards)
        stats["episode_lengths"][episode] = len(episode_rewards)
        stats["average_episode_rewards"][episode] = np.average(episode_rewards)
        stats["learning_rate"][episode] = alpha
        stats["epsilon"][episode] = epsilon
        stats["gamma"][episode] = gamma
        stats["episode"] = episode

        if save_freq and episode % save_freq == 0:
            save(q_values, stats, env.unwrapped.spec.id)

    stats["time_elapsed"] = time.process_time() - start
    print("\n")
    return q_values, stats


def select_action(q_values, state, env, epsilon=0):
    """

    :param q_values: value function to select action greedily according to
    :param state: state
    :param env: Open AI gym environment
    :param epsilon: explore rate
    :return:
    """
    if np.random.random() < epsilon:
        action = env.action_space.sample()
    else:
        action = np.argmax(q_values[state])
    return action


def play(q_values, env, num_episodes, buckets, state_bounds, suppress_print=False,
         episode_length=None):
    """
    renders gym environment with policy acting greedily according to q_values value function
    NOTE: After you use an environment to play you'll need to make a new environment instance to
    use it again because the close function is called
    :param q_values: q_values value function
    :param env: Open AI gym environment that hasn't been closed yet
    :param num_episodes: how many episode to run for
    :param buckets: tuple of ints where the ith value is the number of buckets for ith dimension of
    state
    :param state_bounds: list of tuples where ith tuple contains the min and max value in that order
     of ith dimension
    :param suppress_print: boolean whether to suppress print statements about current episode or not
    :param episode_length: max length of episode
    :return: None
    """
    for i in range(num_episodes):
        obv = env.reset()
        t = 0
        done = False
        while not done:
            if episode_length and t >= episode_length:
                break
            state = discretize_state(buckets, state_bounds, env.bins, obv)
            action = select_action(q_values, state, env, epsilon=0)
            obv, reward, done, _ = env.step(action)
            env.render()
            t += 1
        if not suppress_print:
            print("episode", i,  "lasted", t, "timesteps.")
    env.close()  # this is required to prevent the script from crashing after closing the window
