from tensorflow.keras.models import Model
import tensorflow as tf
import numpy as np
import cv2


class GradCAM:
    def __init__(self, model, target_layer_name=None, target_layer_min_shape=1):
        """
        Store the model, the class index used to measure the class activation map,
        and the target layer to be used when visualizing the class activation map.
        Also, target_layer_min_shape can be set to select the target output layer
        with AT LEAST size target_layer_min_shape x target_layer_min_shape size,
        to compare different model on same heatmap resolution
        """

        config = model.layers[-1].get_config()
        if config['activation'] == 'softmax':
            # store weights
            weights = [x.numpy() for x in model.layers[-1].weights]

            config['activation'] = tf.keras.activations.linear
            config['name'] = 'new_layer'

            new_layer = tf.keras.layers.Dense(**config)(model.layers[-2].output)
            new_model = tf.keras.models.Model(inputs=[model.input], outputs=[new_layer])
            new_model.layers[-1].set_weights(weights)

            self.model = new_model
        else:
            self.model = model

        # if the layer name is None, attempt to automatically find
        # the target output layer
        if target_layer_name is None:
            # attempt to find the final convolutional layer in the network
            # by looping over the layers of the network in reverse order
            for layer in reversed(self.model.layers):
                # check to see if the layer has a 4D output
                if len(layer.output_shape) == 4 and layer.output_shape[1] >= target_layer_min_shape:
                    self.target_layer_name = layer.name
                    return

            # otherwise, we could not find a 4D layer so the GradCAM algorithm cannot be applied
            raise ValueError("Could not find 4D layer. Cannot apply GradCAM.")

        else:
            self.target_layer_name = target_layer_name

    def compute_heatmap(self, image, classIdx, eps=1e-8):
        # construct our gradient model by supplying (1) the inputs
        # to our pre-trained model, (2) the output of the (presumably)
        # final 4D layer in the network, and (3) the output of the
        # softmax activations from the model

        grad_model = Model(inputs=[self.model.inputs], outputs=[self.model.get_layer(self.target_layer_name).output,
                                                                self.model.output])

        # record operations for automatic differentiation
        with tf.GradientTape() as tape:
            # cast the image tensor to a float-32 data type, pass the
            # image through the gradient model, and grab the loss
            # associated with the specific class index
            inputs = tf.cast(image, tf.float32)
            (convOutputs, predictions) = grad_model(inputs)
            loss = predictions[:, classIdx]

        # use automatic differentiation to compute the gradients
        grads = tape.gradient(loss, convOutputs)

        # compute the guided gradients
        castConvOutputs = tf.cast(convOutputs > 0, "float32")
        castGrads = tf.cast(grads > 0, "float32")
        guidedGrads = castConvOutputs * castGrads * grads

        # the convolution and guided gradients have a batch dimension
        # (which we don't need) so let's grab the volume itself and
        # discard the batch
        convOutputs = convOutputs[0]
        guidedGrads = guidedGrads[0]

        # compute the average of the gradient values, and using them
        # as weights, compute the ponderation of the filters with
        # respect to the weights
        weights = tf.reduce_mean(guidedGrads, axis=(0, 1))
        cam = tf.reduce_sum(tf.multiply(weights, convOutputs), axis=-1)

        # grab the spatial dimensions of the input image and resize
        # the output class activation map to match the input image
        # dimensions
        (w, h) = (image.shape[2], image.shape[1])
        heatmap = cv2.resize(cam.numpy(), (w, h))

        # normalize the heatmap such that all values lie in the range
        # [0, 1], scale the resulting values to the range [0, 255],
        # and then convert to an unsigned 8-bit integer
        numer = heatmap - np.min(heatmap)
        denom = (heatmap.max() - heatmap.min()) + eps
        heatmap = numer / denom
        heatmap = (heatmap * 255).astype("uint8")

        # return the resulting heatmap to the calling function
        return heatmap

    def overlay_heatmap(self, heatmap, image, alpha=0.5,
                        colormap=cv2.COLORMAP_VIRIDIS):
        # apply the supplied color map to the heatmap and then
        # overlay the heatmap on the input image
        heatmap = cv2.applyColorMap(heatmap, colormap)
        output = cv2.addWeighted(image, alpha, heatmap, 1 - alpha, 0)

        # return a 2-tuple of the color mapped heatmap and the output,
        # overlaid image
        return heatmap, output
