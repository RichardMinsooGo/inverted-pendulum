import numpy as np
import os
import tensorflow as tf
from tensorflow.examples.tutorials.mnist import input_data
from collections import deque
import random
from simulate import PendulumDynamics


class ReplayBuffer:
    def __init__(self, max_size):
        self.max_size = max_size
        self.buffer = deque()
    
    def add(self, thing):
        self.buffer.appendleft(thing)
        if len(self.buffer) > self.max_size:
            self.buffer.pop()

    def random_sample(self, size):
        if size < len(self.buffer):
            return random.sample(self.buffer, size)
        else:
            return list(self.buffer)

    def is_full(self):
        return len(self.buffer) == self.max_size


class StatusProcessor:
    x_min = -10
    x_max = 10
    xdot_min = -100
    xdot_max = 100
    theta_min = -10
    theta_max = 10
    thetadot_min = -100
    thetadot_max = 100

    @staticmethod
    def _normalize(y, ymin, ymax):
        return 2 * (y - ymin) / (ymax - ymin) - 1

    def normalize(self, status):
        x, xdot, theta, thetadot = status
        return (
            self._normalize(x, self.x_min, self.x_max),
            self._normalize(xdot, self.xdot_min, self.xdot_max),
            self._normalize(theta, self.theta_min, self.theta_max),
            self._normalize(thetadot, self.thetadot_min, self.thetadot_max),
        )

    def is_valid(self, status):
        x, xdot, theta, thetadot = status
        return (
            self.x_min < x < self.x_max
        ) and (
            self.xdot_min < xdot < self.xdot_max
        ) and (
            self.theta_min < theta < self.theta_max
        ) and (
            self.thetadot_min < thetadot < self.thetadot_max
        )
    
    def is_valid_norm(self, status):
        return all(-1 <= x <= 1 for x in status)


def main():
    # network shape
    nnet_state_size = 4
    nnet_action_size = 3
    nnet_hidden_state_size = 16
    nnet_hidden_size = 8

    # learning hyper parameters
    tau = 0.1
    replay_batch_size = 32
    simulation_length = 500
    num_episodes = 100000
    replay_buffer_size = 0.1 * num_episodes * simulation_length
    force_factor = 50
    epsilon_decay = 0.0005
    gamma = 0.9
    save_network_every = 25

    graph = tf.Graph()
    with graph.as_default():
        nnet_input_state = tf.placeholder(
            shape=[None, nnet_state_size], dtype=tf.float32, name='nnet_input_state'
        )
        nnet_input_action = tf.placeholder(
            shape=[None, nnet_action_size], dtype=tf.float32, name='nnet_input_action'
        )

        nnet_label = tf.placeholder(
            shape=[None, 1], dtype=tf.float32, name='nnet_label'
        )

        def make_weights(rows, cols):
            weights = tf.Variable(tf.truncated_normal(shape=[rows, cols], stddev=0.1))
            bias = tf.Variable(tf.constant(0.1, shape=[cols]))
            return weights, bias

        def compute_next_layer(input_layer, weights, bias, activation=tf.nn.relu):
            h = tf.matmul(input_layer, weights) + bias
            return activation(h) if activation else h

        weights_1, bias_1 = make_weights(nnet_state_size, nnet_hidden_state_size)
        hidden_state_1 = compute_next_layer(nnet_input_state, weights_1, bias_1)

        weights_2, bias_2 = make_weights(nnet_hidden_state_size, nnet_hidden_size)
        hidden_state_2 = compute_next_layer(hidden_state_1, weights_2, bias_2)

        weights_3, bias_3 = make_weights(nnet_action_size, nnet_hidden_size)
        hidden_action = compute_next_layer(nnet_input_action, weights_3, bias_3)

        bias_5 = tf.Variable(tf.constant(0.1, shape=[nnet_hidden_size]))
        hidden_combined = tf.nn.relu(hidden_state_2 + hidden_action + bias_5)

        weights_4, bias_4 = make_weights(nnet_hidden_size, 1)
        output = compute_next_layer(hidden_combined, weights_4, bias_4, activation=None)

        squared_error = (nnet_label - output)**2
        loss = tf.reduce_mean(squared_error)

        optimizer = tf.train.AdamOptimizer(0.001).minimize(loss)

        network_params = [weights_1, bias_1, weights_2,
                          bias_2, weights_3, bias_3,
                          weights_4, bias_4, bias_5]
        target_network_params = [tf.Variable(var.initialized_value())
                                 for var in network_params]
        update_target_network = [
            target_var.assign(tau * var + (1 - tau) * target_var)
            for var, target_var in zip(network_params, target_network_params)
        ]

        (
            target_weights_1, target_bias_1, target_weights_2,
            target_bias_2, target_weights_3, target_bias_3,
            target_weights_4, target_bias_4, target_bias_5
        ) = target_network_params

        target_hidden_state_1 = compute_next_layer(nnet_input_state, target_weights_1, target_bias_1)
        target_hidden_state_2 = compute_next_layer(target_hidden_state_1, target_weights_2, target_bias_2)
        target_hidden_action = compute_next_layer(nnet_input_action, target_weights_3, target_bias_3)
        target_hidden_combined = tf.nn.relu(target_hidden_state_2 + target_hidden_action + target_bias_5)
        target_output = compute_next_layer(target_hidden_combined, target_weights_4, target_bias_4, activation=None)

    for f in os.listdir('logs'):
        os.remove('logs/' + f)

    with tf.Session(graph=graph) as session:
        tf.global_variables_initializer().run()

        writer = tf.summary.FileWriter('logs', session.graph)
        saver = tf.train.Saver(network_params)
        replay_buffer = ReplayBuffer(replay_buffer_size)
        state_processor = StatusProcessor()

        for episode in range(num_episodes):
            pendulum = PendulumDynamics(0, 0, np.pi, 0)

            if episode and not episode % save_network_every:
                # TODO save framebuffer's contents as well!
                saver.save(session, './logs/updates', global_step=episode)
                with open('./logs/last.csv', 'w') as f:
                    f.write('x;xdot;theta;thetadot\n')
                    for state in last_episode:
                        f.write('%f;%f;%f;%f\n' % state)
                print('saved')

            end_early, last_episode, all_losses, all_rewards = False, [], [], []
            for step in range(simulation_length):
                state = state_processor.normalize(pendulum.state)
                last_episode.append(state)

                # choose next action
                if np.random.random() < np.exp(-episode * epsilon_decay):
                    action = random.choice([-1, 0, 1])
                else:
                    q_vals = session.run(output, feed_dict={
                        nnet_input_state: np.array([state] * 3),
                        nnet_input_action: np.array([
                            [1, 0, 0], [0, 1, 0], [0, 0, 1]
                        ], dtype=np.float32)
                    })
                    if q_vals[0][0] >= q_vals[1][0] and q_vals[0][0] >= q_vals[2][0]:
                        action = -1
                    elif q_vals[1][0] >= q_vals[0][0] and q_vals[1][0] >= q_vals[2][0]:
                        action = 0
                    else:  # if q_vals[2][0] >= q_vals[1][0] and q_vals[2][0] >= q_vals[0][0]:
                        action = 1

                # perform action
                old_state = state
                pendulum.step_simulate(force_factor * action)
                state = state_processor.normalize(pendulum.state)

                if state_processor.is_valid_norm(state):
                    #reward = -0.05 * (abs(pendulum.theta) - 1) - 0.005 * (abs(pendulum.x) - 1)
                    reward = 0.1 if abs(pendulum.theta) < 0.25 and abs(pendulum.x < 0.25) else -0.001
                else:
                    reward = -1
                    end_early = True

                replay_buffer.add((old_state, action, reward, state))
                all_rewards.append(reward)

                if end_early:
                    break

                batch_replay = replay_buffer.random_sample(replay_batch_size)

                # get q value for each action in the batch
                next_moves_q_state, next_moves_q_action = [], []
                for state, action, reward, next_state in batch_replay:
                    next_moves_q_state.extend([next_state] * 3)
                    next_moves_q_action.extend([
                        [1, 0, 0], [0, 1, 0], [0, 0, 1]
                    ])

                q_vals = session.run(target_output, feed_dict={
                    nnet_input_state: np.array(next_moves_q_state),
                    nnet_input_action: np.array(next_moves_q_action, dtype=np.float32)
                })

                # compute expected reward for the states in the batch
                batch_inputs_state, batch_inputs_action, batch_outputs = [], [], []
                for i, (state, action, reward, next_state) in enumerate(batch_replay):
                    batch_inputs_state.append(state)
                    batch_inputs_action.append([(0, 1, 0), (0, 0, 1), (1, 0, 0)][action])
                    consider_future = 1 if state_processor.is_valid_norm(next_state) else 0
                    batch_outputs.append([reward + consider_future * gamma * max(
                        q_vals[3 * i][0], q_vals[3 * i + 1][0], q_vals[3 * i + 2][0])
                    ])

                # backpropagate
                _, loss_value = session.run([optimizer, loss], feed_dict={
                    nnet_input_state: batch_inputs_state,
                    nnet_input_action: batch_inputs_action,
                    nnet_label: batch_outputs,
                })
                assert not np.isnan(loss_value)
                all_losses.append(loss_value)

                # update target network
                session.run(update_target_network)

            summary = tf.Summary()
            summary.value.add(tag='loss', simple_value=np.mean(all_losses))
            summary.value.add(tag='reward', simple_value=sum(all_rewards))
            writer.add_summary(summary, global_step=episode)
            print('Episode %d - L: %.3f\tAR: %.3f\tSR: %.3f' % (
                episode, np.mean(all_losses), np.mean(all_rewards), sum(all_rewards))
            )

        writer.close()


if __name__ == '__main__':
    main()